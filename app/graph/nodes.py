"""Graph nodes: plan, agent (reason/act), approval (human-in-the-loop), reflect.

Tool *execution* is handled by a prebuilt ToolNode wired up in builder.py; these
nodes provide the reasoning and control logic around it.
"""

from __future__ import annotations

import json
import logging
import re
import time

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from langgraph.types import interrupt
from pydantic import BaseModel, Field

from app import incident_memory, policy, replay, routing
from app.config import get_settings
from app.graph.prompts import (
    AGENT_SYSTEM,
    DEFENDER_SYSTEM,
    PLANNER_SYSTEM,
    PROSECUTOR_SYSTEM,
    REFLECT_SYSTEM,
    REPORT_SYSTEM,
    VERIFY_SYSTEM,
)
from app.graph.sandbox import run_counterfactual
from app.graph.state import AgentState
from app.llm import cached_system, get_llm

log = logging.getLogger("devcopilot.agent")


def _log_usage(node: str, resp) -> int:
    """Log per-call LLM token usage so cost is observable (carries the request-id
    via the logging filter) and RETURN this call's total tokens so the node can
    add it to the run budget. usage_metadata is absent on some providers — 0 then."""
    um = getattr(resp, "usage_metadata", None)
    if not um:
        return 0
    details = um.get("input_token_details") or {}
    total = um.get("total_tokens") or 0
    # Surface BOTH cache_read and cache_creation: if prompt caching is enabled but
    # cache_creation stays 0 across a run (e.g. the cacheable prefix is below the
    # model's minimum because the tool set was narrowed), the cache_control marker
    # is a silent no-op — this makes that visible instead of an invisible cost.
    log.info(
        "llm_usage node=%s input=%s output=%s total=%s cache_read=%s cache_creation=%s",
        node,
        um.get("input_tokens"),
        um.get("output_tokens"),
        total,
        details.get("cache_read"),
        details.get("cache_creation"),
    )
    try:
        return int(total)
    except (TypeError, ValueError):
        return 0


def _over_token_budget(tokens_used: int, settings) -> bool:
    """True when the run has spent its per-investigation token budget (0 = off).
    Pure so the cost kill-switch logic is unit-testable without an LLM."""
    cap = settings.copilot_max_tokens_per_run
    return bool(cap) and tokens_used >= cap


def _last_user_text(state: AgentState) -> str:
    for msg in reversed(state["messages"]):
        if isinstance(msg, HumanMessage):
            return msg.content if isinstance(msg.content, str) else str(msg.content)
    return ""


def _history_digest(state: AgentState, max_exchanges: int = 4, max_chars: int = 400) -> str:
    """Compact record of PRIOR exchanges (earlier user questions + the agent's
    final answers), excluding the current request and all tool traffic. Lets the
    planner build on a multi-turn conversation without re-reading huge tool outputs."""
    msgs = state.get("messages", [])
    last_user = -1
    for i in range(len(msgs) - 1, -1, -1):
        if isinstance(msgs[i], HumanMessage):
            last_user = i
            break
    prior = msgs[:last_user] if last_user > 0 else []
    lines: list[str] = []
    for m in prior:
        if isinstance(m, HumanMessage):
            text = m.content if isinstance(m.content, str) else str(m.content)
            lines.append(f"User: {text.strip()[:max_chars]}")
        elif isinstance(m, AIMessage) and not m.tool_calls and str(m.content).strip():
            lines.append(f"Assistant: {str(m.content).strip()[:max_chars]}")
    return "\n".join(lines[-(max_exchanges * 2):])


def _prior_incidents_block(request: str, limit: int = 2) -> str:
    """Warm-start context: prior similar incidents from the incident-memory corpus,
    so the plan benefits from institutional memory ('have we seen this before?')
    automatically. Phrased as priors to VERIFY, not facts to assume, to avoid
    anchoring the agent on a stale match. Best-effort: never breaks planning."""
    try:
        hits = incident_memory.search(request, limit=limit)
    except Exception:  # noqa: BLE001
        return ""
    if not hits:
        return ""
    lines = ["Possibly-related PRIOR incidents (verify against live evidence, don't assume):"]
    for h in hits:
        rc = h.get("root_cause", "?")
        lines.append(f"- {h.get('title', h.get('id'))} — past root cause: {rc}")
    return "\n".join(lines)


def make_plan_node():
    """Node: turn the request into a short investigation plan.

    Uses the cheap fast model — planning is lightweight and doesn't need tools.
    """
    llm = get_llm(fast=True)

    def plan_node(state: AgentState) -> dict:
        request = _last_user_text(state)
        # On a follow-up, give the planner what was already investigated so it
        # builds on the conversation instead of re-planning from scratch.
        digest = _history_digest(state)
        priors = _prior_incidents_block(request)
        human = f"Conversation so far:\n{digest}\n\nNew request:\n{request}" if digest else request
        if priors:
            human = f"{priors}\n\n{human}"
        resp = llm.invoke(
            [SystemMessage(content=PLANNER_SYSTEM), HumanMessage(content=human)]
        )
        tokens = _log_usage("plan", resp)
        plan = [ln.strip() for ln in str(resp.content).splitlines() if ln.strip()]
        return {
            "plan": plan,
            "iteration": 0,
            "status": "investigating",
            "feedback": "",
            "tokens_used": tokens,
        }

    return plan_node


def make_agent_node(tools):
    """Node: the reasoning loop. Binds tools and lets Claude decide the next
    action (call a tool) or produce a final answer (no tool calls).

    Every agent call increments `iteration`. Once the cap is reached the agent
    is invoked WITHOUT tools and told to give its final answer — this bounds the
    agent<->tools hot loop while guaranteeing the run never ends on a message
    with unexecuted tool_calls (which would corrupt history / 400 on Anthropic).
    """
    settings = get_settings()
    # Build both tiers up front so the agent can triage per request (model routing):
    # the main reasoning model for real incidents, the cheap fast model for simple
    # informational lookups. Falls back to main on any doubt (see app/routing.py).
    llm_tools = get_llm().bind_tools(tools)
    llm_plain = get_llm()  # no tools — used to force a final answer at the cap
    llm_tools_fast = get_llm(fast=True).bind_tools(tools)
    llm_plain_fast = get_llm(fast=True)

    def agent_node(state: AgentState) -> dict:
        iteration = state.get("iteration", 0) + 1
        plan_text = "\n".join(state.get("plan", [])) or "(no explicit plan)"
        feedback = state.get("feedback", "")
        # Force a final answer at EITHER the step cap or the token-budget ceiling —
        # both stop the agent<->tools loop without stranding tool calls.
        over_budget = _over_token_budget(state.get("tokens_used", 0), settings)
        at_cap = iteration >= settings.copilot_max_iterations or over_budget

        # Triage: a clearly-informational request runs on the fast model; anything
        # incident-shaped stays on the main reasoning model.
        fast = routing.use_fast_model(_last_user_text(state), settings.copilot_model_routing)
        tools_llm = llm_tools_fast if fast else llm_tools
        plain_llm = llm_plain_fast if fast else llm_plain

        # The agent system prompt + plan is the stable, cacheable prefix (constant
        # across this run's loop iterations); per-iteration text (cap notice or
        # reviewer feedback) goes after the cache breakpoint so it never busts it.
        stable = AGENT_SYSTEM.format(plan=plan_text)
        if at_cap:
            limit = "token budget" if over_budget else "investigation step limit"
            volatile = (
                f"You have reached the {limit}. Do NOT call any tools. "
                "Summarize the root cause and your recommendation now."
            )
            resp = plain_llm.invoke([cached_system(stable, volatile), *state["messages"]])
        else:
            # The reflect node judged the last answer incomplete — tell the agent
            # exactly what to close so it doesn't repeat itself.
            volatile = (
                "Reviewer feedback on your previous answer — address this now "
                f"before finishing:\n{feedback}"
                if feedback
                else ""
            )
            resp = tools_llm.invoke([cached_system(stable, volatile), *state["messages"]])

        tokens = _log_usage("agent", resp)
        return {"messages": [resp], "iteration": iteration, "tokens_used": tokens}

    return agent_node


def approval_node(state: AgentState) -> dict:
    """Human-in-the-loop gate for write actions.

    Pauses the graph with `interrupt()`, surfacing the pending tool call to the
    caller. Execution resumes when the caller sends Command(resume={...}).
      - approved  -> let the message stand so the ToolNode executes it
      - rejected  -> answer the tool calls with rejection ToolMessages and
                     hand control back to the agent to choose another path
    """
    last = state["messages"][-1]
    all_calls = list(getattr(last, "tool_calls", []))

    # Surface EVERY tool call that will run (the ToolNode executes the whole
    # message on approval), each classified by the policy engine: decision class
    # (allow/notify/approve), risk tier, why, and a human-readable impact preview —
    # so the reviewer sees what will happen, not just a tool name, and isn't shown
    # only the write while silently approving co-bundled reads.
    actions = []
    for c in all_calls:
        cls = policy.classify(c["name"], c.get("args"))
        actions.append(
            {
                "tool": c["name"],
                "args": c["args"],
                "write": cls["write"],
                "decision": cls["decision"],
                "risk": cls["risk"],
                "why": cls["why"],
                "preview": cls["preview"],
            }
        )
    highest = "high" if any(a["risk"] == "high" for a in actions) else (
        "medium" if any(a["risk"] == "medium" for a in actions) else "low"
    )
    # How much did the agent investigate before proposing this write? Surface it so
    # the reviewer can weigh a write proposed on thin evidence more carefully, and
    # compute the confidence gate that blocks a programmatic auto-approval of a
    # low-evidence, high-risk write (a human can still approve it explicitly).
    evidence_count = sum(1 for m in state["messages"] if isinstance(m, ToolMessage))
    gate = policy.confidence_gate(all_calls, evidence_count)
    decision = interrupt(
        {
            "type": "approval_request",
            "message": "The agent wants to run an action that needs your approval.",
            "risk": highest,
            "evidence_count": evidence_count,
            "confidence": gate["confidence"],
            "auto_approve_blocked": gate["auto_approve_blocked"],
            "gate_reason": gate["reason"],
            "actions": actions,
        }
    )

    approved = bool(decision.get("approved")) if isinstance(decision, dict) else bool(decision)
    if approved:
        # Leave the AIMessage untouched -> routed to ToolNode for execution.
        return {"pending_action": None}

    # Rejected: every tool_call id must still be answered to keep history valid.
    reason = (decision or {}).get("reason", "Rejected by human reviewer.") if isinstance(
        decision, dict
    ) else "Rejected by human reviewer."
    rejections = [
        ToolMessage(
            content=f"ACTION REJECTED by human: {reason}. Do not retry this exact "
            f"action; summarize findings or propose an alternative.",
            tool_call_id=c["id"],
        )
        for c in last.tool_calls
    ]
    return {"messages": rejections, "pending_action": None}


def make_reflect_node():
    """Node: decide whether to finish or keep investigating.

    Token-efficient: runs on the cheap fast model and judges only the agent's
    latest answer (plus the original request) instead of re-sending the full
    transcript with its large tool outputs.
    """
    llm = get_llm(fast=True)
    settings = get_settings()

    def reflect_node(state: AgentState) -> dict:
        # `iteration` is incremented in agent_node; reflect only reads it. Also
        # stop reflecting once the token budget is spent — go straight to report.
        if state.get("iteration", 0) >= settings.copilot_max_iterations or _over_token_budget(
            state.get("tokens_used", 0), settings
        ):
            return {"status": "done", "feedback": ""}

        request = _last_user_text(state)
        latest_answer = ""
        for msg in reversed(state["messages"]):
            if isinstance(msg, AIMessage) and not msg.tool_calls:
                latest_answer = str(msg.content)
                break

        resp = llm.invoke(
            [
                SystemMessage(content=REFLECT_SYSTEM),
                HumanMessage(
                    content=f"Original request:\n{request}\n\n"
                    f"Agent's latest answer:\n{latest_answer}\n\n"
                    "Is the investigation complete?"
                ),
            ]
        )
        tokens = _log_usage("reflect", resp)
        text = str(resp.content).strip()
        if text.upper().startswith("DONE"):
            return {"status": "done", "feedback": "", "tokens_used": tokens}
        # CONTINUE — capture the gap note (everything after the verdict word) so
        # the next agent pass closes it instead of re-emitting the same answer.
        rest = text[len("CONTINUE"):] if text.upper().startswith("CONTINUE") else text
        feedback = rest.lstrip(" :\n-").strip()[:500]
        return {"status": "investigating", "feedback": feedback, "tokens_used": tokens}

    return reflect_node


# --------------------------------------------------------------------------- #
# Report node — synthesize a structured, evidence-grounded RCA deliverable.
# This is the product's core output: instead of a freeform paragraph, every
# finished investigation produces a typed RCA object (ranked hypotheses with
# verdicts + cited evidence, severity, confidence, recommended actions) plus a
# rendered blameless postmortem. All parsing/rendering lives in pure helpers so
# it's unit-testable without an LLM, and it degrades gracefully: a non-JSON or
# malformed model reply falls back to a minimal report built from the final text,
# so the report node can never break a completed run.
# --------------------------------------------------------------------------- #
_SEVERITIES = {"SEV1", "SEV2", "SEV3", "SEV4", "INFO"}
_CONFIDENCE = {"high", "medium", "low"}
_VERDICTS = {"validated", "invalidated", "inconclusive"}


def _evidence_digest(state: AgentState, max_items: int = 24, max_chars: int = 600) -> str:
    """Compact record of what the tools actually returned, so the reporter grounds
    the RCA in observed evidence rather than re-imagining the investigation."""
    lines: list[str] = []
    for m in state.get("messages", []):
        if isinstance(m, ToolMessage):
            name = getattr(m, "name", "tool")
            content = m.content if isinstance(m.content, str) else str(m.content)
            lines.append(f"[{name}] {content.strip()[:max_chars]}")
        elif isinstance(m, AIMessage) and m.tool_calls:
            for c in m.tool_calls:
                lines.append(f"(called {c['name']} {json.dumps(c.get('args', {}), default=str)[:200]})")
    return "\n".join(lines[-max_items:])


def _coerce_str_list(value, limit: int = 20, max_chars: int = 400) -> list[str]:
    """Coerce an arbitrary JSON value into a clean list[str] (LLMs return mixed shapes)."""
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, list):
        return []
    out: list[str] = []
    for item in value:
        text = item if isinstance(item, str) else json.dumps(item, default=str)
        text = text.strip()
        if text:
            out.append(text[:max_chars])
        if len(out) >= limit:
            break
    return out


def _normalize_report(data: dict, fallback_summary: str) -> dict:
    """Coerce a parsed model dict into the canonical RCA shape with safe defaults.
    Pure + total: any odd input yields a valid report rather than raising."""
    sev = str(data.get("severity", "")).strip().upper()
    severity = sev if sev in _SEVERITIES else "SEV3"
    conf = str(data.get("confidence", "")).strip().lower()
    confidence = conf if conf in _CONFIDENCE else "low"

    root_cause = data.get("root_cause")
    root_cause = root_cause.strip() if isinstance(root_cause, str) and root_cause.strip() else None

    hypotheses: list[dict] = []
    raw_hyps = data.get("hypotheses")
    if isinstance(raw_hyps, list):
        for h in raw_hyps:
            if not isinstance(h, dict):
                continue
            cause = str(h.get("cause", "")).strip()
            if not cause:
                continue
            verdict = str(h.get("verdict", "")).strip().lower()
            hconf = str(h.get("confidence", "")).strip().lower()
            hypotheses.append(
                {
                    "cause": cause[:400],
                    "verdict": verdict if verdict in _VERDICTS else "inconclusive",
                    "confidence": hconf if hconf in _CONFIDENCE else "low",
                    "evidence": _coerce_str_list(h.get("evidence"), limit=8),
                }
            )
            if len(hypotheses) >= 10:
                break

    summary = data.get("summary")
    summary = summary.strip() if isinstance(summary, str) and summary.strip() else fallback_summary.strip()
    return {
        "summary": summary[:1500] or "(no summary produced)",
        "severity": severity,
        "confidence": confidence,
        "root_cause": root_cause[:400] if root_cause else None,
        "affected_services": _coerce_str_list(data.get("affected_services"), limit=12, max_chars=80),
        "hypotheses": hypotheses,
        "evidence": _coerce_str_list(data.get("evidence"), limit=20),
        "recommended_actions": _coerce_str_list(data.get("recommended_actions"), limit=12),
    }


def _parse_report(text: str, fallback_summary: str) -> dict:
    """Extract the JSON object from a model reply and normalize it. On any failure,
    return a minimal valid report built from the final answer text."""
    fallback: dict = {
        "summary": fallback_summary.strip()[:1500] or "(no summary produced)",
        "severity": "SEV3",
        "confidence": "low",
        "root_cause": None,
        "affected_services": [],
        "hypotheses": [],
        "evidence": [],
        "recommended_actions": [],
    }
    if not text or not text.strip():
        return fallback
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end <= start:
        return fallback
    try:
        data = json.loads(text[start : end + 1])
    except (ValueError, TypeError):
        return fallback
    if not isinstance(data, dict):
        return fallback
    return _normalize_report(data, fallback_summary)


def _calibrate_confidence(report: dict) -> dict:
    """Deterministically calibrate confidence from evidence density and flag
    INSUFFICIENT EVIDENCE, so a thin investigation can't emit a falsely confident
    verdict (LLMs are positively biased). Pure + testable. Adds:
      calibrated_confidence (high/medium/low), abstained (bool), needs (gaps).
    The LLM's own per-hypothesis confidence is left intact alongside this."""
    evidence_count = len(report.get("evidence") or [])
    hyps = report.get("hypotheses") or []
    validated = [h for h in hyps if h.get("verdict") == "validated"]
    hyp_evidence = sum(len(h.get("evidence") or []) for h in hyps)
    has_rc = bool(report.get("root_cause"))
    total_evidence = evidence_count + hyp_evidence

    if total_evidence >= 3 and validated and has_rc:
        cal = "high"
    elif total_evidence >= 2 and (validated or has_rc):
        cal = "medium"
    else:
        cal = "low"
    abstained = total_evidence < 2 or (not validated and not has_rc)

    needs: list[str] = []
    if abstained:
        if not has_rc:
            needs.append("Establish and state a single most-likely root cause.")
        if not validated:
            needs.append("Validate a hypothesis against telemetry (logs/metrics/traces).")
        if total_evidence < 2:
            needs.append("Gather more cited evidence (exact log lines, metric values, file:line, commits).")

    report["calibrated_confidence"] = cal
    report["abstained"] = abstained
    report["needs"] = needs
    return report


_GROUND_STOP = {
    "the", "and", "for", "with", "that", "this", "from", "into", "was", "were",
    "has", "have", "not", "but", "its", "are", "out", "due", "see", "via",
}


def _salient_tokens(text: str) -> set[str]:
    return {t for t in re.findall(r"[a-z0-9_./:]{3,}", (text or "").lower())} - _GROUND_STOP


def _verify_grounding(report: dict, evidence_digest: str) -> dict:
    """Deterministic critic pass: check that the report's cited evidence actually
    appears in the REAL tool output (the evidence digest). An evidence item whose
    salient tokens barely overlap the observed data is likely fabricated/overstated.
    If the verdict isn't corroborated, downgrade confidence and abstain. Pure +
    free (no extra LLM call), so the critic itself can't hallucinate.

    Adds report["grounding"] = {checked, grounded, ratio, ungrounded_examples}.
    """
    digest_tokens = _salient_tokens(evidence_digest)
    items = list(report.get("evidence") or [])
    for h in report.get("hypotheses") or []:
        items += list(h.get("evidence") or [])

    if not digest_tokens or not items:
        report["grounding"] = {"checked": 0, "grounded": 0, "ratio": None,
                               "ungrounded_examples": []}
        return report

    grounded = 0
    ungrounded: list[str] = []
    for ev in items:
        toks = _salient_tokens(ev)
        if not toks:
            continue
        overlap = len(toks & digest_tokens) / len(toks)
        if overlap >= 0.4:
            grounded += 1
        else:
            ungrounded.append(ev)
    checked = grounded + len(ungrounded)
    ratio = grounded / checked if checked else None
    report["grounding"] = {
        "checked": checked, "grounded": grounded, "ratio": round(ratio, 2) if ratio is not None else None,
        "ungrounded_examples": ungrounded[:3],
    }
    # Poorly-corroborated verdict (most cited evidence isn't in the observed data)
    # -> don't present it as confident.
    if checked >= 2 and ratio is not None and ratio < 0.5:
        report["calibrated_confidence"] = "low"
        report["abstained"] = True
        needs = list(report.get("needs") or [])
        needs.append("Cited evidence is not corroborated by the observed tool output — re-verify each claim against logs/metrics/traces.")
        report["needs"] = needs
    return report


def _render_postmortem(report: dict, request: str) -> str:
    """Render a blameless, copy-pasteable postmortem from the RCA object. Pure:
    deterministic Markdown, no extra LLM call. Roles/actions, never names."""
    rc = report.get("root_cause") or "Inconclusive — see hypotheses below."
    services = ", ".join(report.get("affected_services") or []) or "—"
    lines = [
        "# Incident Postmortem",
        "",
        f"**Severity:** {report.get('severity', 'SEV3')}  ·  "
        f"**Confidence:** {report.get('calibrated_confidence', report.get('confidence', 'low'))}  ·  "
        f"**Affected services:** {services}",
    ]
    if report.get("abstained"):
        lines += [
            "",
            "> ⚠️ **Insufficient evidence** — this is a provisional read, not a confirmed "
            "root cause. To raise confidence: " + "; ".join(report.get("needs") or []),
        ]
    lines += [
        "",
        "## Summary",
        report.get("summary", "").strip() or "—",
        "",
        "## Root cause",
        rc,
        "",
        "## What was investigated",
        f"> Triggering request: {request.strip()[:500]}" if request.strip() else "> —",
    ]
    critique = report.get("critique")
    if critique and critique.get("verdict") and critique["verdict"] != "upheld":
        lines += ["", "## Adversarial review"]
        lines.append(f"- **Verdict:** root cause _{critique['verdict']}_ by the prosecutor/defender panel")
        for s in critique.get("standing_objections") or []:
            lines.append(f"    - unrebutted ({s.get('severity', 'low')}): {s.get('claim', '')}")
    hyps = report.get("hypotheses") or []
    if hyps:
        lines += ["", "## Hypotheses considered"]
        for h in hyps:
            lines.append(f"- **{h['cause']}** — _{h['verdict']}_ (confidence: {h['confidence']})")
            for ev in h.get("evidence", []):
                lines.append(f"    - {ev}")
    evidence = report.get("evidence") or []
    if evidence:
        lines += ["", "## Evidence"]
        lines += [f"- {e}" for e in evidence]
    actions = report.get("recommended_actions") or []
    if actions:
        lines += ["", "## Recommended actions / follow-ups"]
        lines += [f"- [ ] {a}" for a in actions]
    verification = report.get("verification")
    if verification and verification.get("verdict") not in (None, "no_fix_proposed"):
        lines += ["", "## Fix verification"]
        lines.append(
            f"- **Verdict:** {verification.get('verdict', 'inconclusive')} "
            f"(confidence: {verification.get('confidence', 'low')})"
        )
        if verification.get("rationale"):
            lines.append(f"- {verification['rationale']}")
        sandbox = verification.get("sandbox")
        if sandbox and sandbox.get("verdict") not in (None, "no_patch"):
            lines.append(
                f"- **Sandbox counterfactual:** `{sandbox.get('verdict')}` — {sandbox.get('detail', '')}"
            )
        criteria = verification.get("resolution_criteria") or []
        if criteria:
            lines.append("- **Resolution criteria — confirm before closing:**")
            lines += [f"    - [ ] {c}" for c in criteria]
        risks = verification.get("residual_risks") or []
        if risks:
            lines.append("- **Residual risks:**")
            lines += [f"    - {r}" for r in risks]
    lines += [
        "",
        "---",
        "_Generated by DevOps Copilot. Blameless by design — this describes systems "
        "and actions, not individuals. Review before sharing._",
    ]
    return "\n".join(lines)


# Schema for `with_structured_output` — mirrors REPORT_SYSTEM's JSON contract.
# Used to make the model's RCA *schema-guaranteed* (it returns a validated object,
# not free text we hope is JSON). Loose field types + defaults so the provider's
# tool-schema is easy to satisfy; _normalize_report still tightens every value.
class _HypothesisSchema(BaseModel):
    cause: str = ""
    verdict: str = "inconclusive"  # validated | invalidated | inconclusive
    confidence: str = "low"  # high | medium | low
    evidence: list[str] = Field(default_factory=list)


class _RcaSchema(BaseModel):
    summary: str = ""
    severity: str = "SEV3"  # SEV1..SEV4 | info
    confidence: str = "low"  # high | medium | low
    root_cause: str | None = None
    affected_services: list[str] = Field(default_factory=list)
    hypotheses: list[_HypothesisSchema] = Field(default_factory=list)
    evidence: list[str] = Field(default_factory=list)
    recommended_actions: list[str] = Field(default_factory=list)


# --------------------------------------------------------------------------- #
# Adversarial RCA critique — a Prosecutor tries to refute the root cause; a
# Defender rebuts using only observed evidence; a deterministic judge downgrades
# or abstains when a serious objection stands unrebutted. Cuts confident-but-wrong
# RCAs (LLMs are positively biased). Parsing + judging are pure/testable; the two
# LLM calls are gated to replay-off so the golden cassette layer stays symmetric.
# --------------------------------------------------------------------------- #
def _extract_json_obj(text: str) -> dict:
    """Best-effort: pull the first JSON object out of a model reply. {} on failure."""
    if not text or "{" not in text:
        return {}
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end <= start:
        return {}
    try:
        data = json.loads(text[start : end + 1])
    except (ValueError, TypeError):
        return {}
    return data if isinstance(data, dict) else {}


def _parse_objections(text: str) -> list[dict]:
    raw = _extract_json_obj(text).get("objections")
    out: list[dict] = []
    if isinstance(raw, list):
        for o in raw:
            if not isinstance(o, dict):
                continue
            claim = str(o.get("claim", "")).strip()
            if not claim:
                continue
            sev = str(o.get("severity", "")).strip().lower()
            out.append({"claim": claim[:300], "severity": sev if sev in _CONFIDENCE else "low"})
            if len(out) >= 5:
                break
    return out


def _parse_rebuttals(text: str) -> list[dict]:
    raw = _extract_json_obj(text).get("rebuttals")
    out: list[dict] = []
    if isinstance(raw, list):
        for r in raw:
            if not isinstance(r, dict):
                continue
            out.append(
                {
                    "objection": str(r.get("objection", "")).strip()[:300],
                    "rebutted": bool(r.get("rebutted")),
                    "evidence": str(r.get("evidence", "")).strip()[:300],
                }
            )
    return out


def _judge_critique(objections: list[dict], rebuttals: list[dict]) -> dict:
    """Deterministic verdict: an objection stands if its (index-aligned) rebuttal is
    missing or rebutted=false. A standing HIGH objection refutes the RCA; a standing
    MEDIUM weakens it; otherwise it's upheld. Pure + testable."""
    standing: list[dict] = []
    for i, o in enumerate(objections):
        rebutted = rebuttals[i].get("rebutted", False) if i < len(rebuttals) else False
        if not rebutted:
            standing.append({"claim": o.get("claim", ""), "severity": o.get("severity", "low")})
    if any(s["severity"] == "high" for s in standing):
        verdict = "refuted"
    elif any(s["severity"] == "medium" for s in standing):
        verdict = "weakened"
    else:
        verdict = "upheld"
    return {
        "verdict": verdict,
        "standing_objections": standing[:5],
        "objection_count": len(objections),
        "standing_count": len(standing),
    }


def _adversarial_critique(llm, request: str, report: dict, digest: str) -> dict:
    """Run Prosecutor → Defender → deterministic judge over the RCA. Returns
    {verdict, objections, rebuttals, standing_objections, tokens}. Never raises."""
    result: dict = {
        "verdict": "upheld", "objections": [], "rebuttals": [],
        "standing_objections": [], "tokens": 0,
    }
    rc = (report.get("root_cause") or "").strip()
    if not rc:
        return result
    ev = digest or "(none recorded)"
    tokens = 0
    try:
        pros = llm.invoke([
            SystemMessage(content=PROSECUTOR_SYSTEM),
            HumanMessage(content=f"Root cause:\n{rc}\n\nSummary:\n{report.get('summary', '')}\n\nEvidence:\n{ev}"),
        ])
        tokens += _log_usage("prosecutor", pros)
        objections = _parse_objections(str(pros.content))
    except Exception:  # noqa: BLE001 — critique must never break a finished run
        log.warning("prosecutor critique failed (non-fatal)", exc_info=True)
        return result
    if not objections:
        result["tokens"] = tokens
        return result  # nothing to refute — RCA stands
    try:
        obj_text = "\n".join(f"- ({o['severity']}) {o['claim']}" for o in objections)
        dfn = llm.invoke([
            SystemMessage(content=DEFENDER_SYSTEM),
            HumanMessage(content=f"Root cause:\n{rc}\n\nEvidence:\n{ev}\n\nObjections:\n{obj_text}"),
        ])
        tokens += _log_usage("defender", dfn)
        rebuttals = _parse_rebuttals(str(dfn.content))
    except Exception:  # noqa: BLE001
        log.warning("defender critique failed (non-fatal)", exc_info=True)
        rebuttals = []  # unrebutted -> objections stand (conservative)
    judged = _judge_critique(objections, rebuttals)
    result.update(objections=objections, rebuttals=rebuttals, tokens=tokens, **judged)
    return result


_DOWNGRADE = {"high": "medium", "medium": "low", "low": "low"}


def _apply_critique(report: dict, critique: dict) -> dict:
    """Fold an adversarial verdict into the report's confidence. A refuted RCA
    abstains; a weakened one drops a confidence notch. Pure + testable."""
    report["critique"] = {
        "verdict": critique["verdict"],
        "objections": critique.get("objections", []),
        "rebuttals": critique.get("rebuttals", []),
        "standing_objections": critique.get("standing_objections", []),
    }
    if critique["verdict"] == "refuted":
        report["calibrated_confidence"] = "low"
        report["abstained"] = True
        needs = list(report.get("needs") or [])
        top = critique["standing_objections"][0]["claim"] if critique.get("standing_objections") else ""
        needs.append(f"Adversarial review raised an unrebutted objection to the root cause: {top}")
        report["needs"] = needs
    elif critique["verdict"] == "weakened":
        cur = report.get("calibrated_confidence") or report.get("confidence") or "low"
        report["calibrated_confidence"] = _DOWNGRADE.get(cur, "low")
    return report


def make_report_node():
    """Node: compile the finished investigation into a structured RCA report +
    postmortem. Runs once when the investigation is done (after reflect)."""
    # Use the fast model: it's a synthesis-from-given-text task (cheap), and it
    # avoids the adaptive-thinking + forced-tool conflict on the main model.
    llm = get_llm(fast=True)
    settings = get_settings()

    def report_node(state: AgentState) -> dict:
        request = _last_user_text(state)
        final_answer = ""
        for msg in reversed(state["messages"]):
            if isinstance(msg, AIMessage) and not msg.tool_calls and str(msg.content).strip():
                final_answer = str(msg.content)
                break
        digest = _evidence_digest(state)
        human = (
            f"Original request:\n{request}\n\n"
            f"Agent's final answer:\n{final_answer}\n\n"
            f"Evidence gathered (tool calls + results):\n{digest or '(none recorded)'}"
        )
        msgs = [SystemMessage(content=REPORT_SYSTEM), HumanMessage(content=human)]
        tokens = 0
        report: dict | None = None
        # Prefer schema-guaranteed structured output (provider-neutral via
        # LangChain) so the RCA is a validated object, not free text we parse and
        # hope is JSON. Gated to OFF mode only — record/replay both use the single
        # text-parse path below, because the cassette layer wraps .invoke() but not
        # with_structured_output(), so recording it would be invisible and replay
        # would miss the key. On any failure it also falls back, so this is a strict
        # upgrade in production and record/replay-symmetric for the golden gate.
        if replay.mode() == "off":
            try:
                structured = llm.with_structured_output(_RcaSchema, include_raw=True)
                out = structured.invoke(msgs)
                tokens = _log_usage("report", out.get("raw"))
                parsed = out.get("parsed")
                if parsed is not None and not out.get("parsing_error"):
                    report = _normalize_report(parsed.model_dump(), fallback_summary=final_answer)
            except Exception:  # noqa: BLE001 — fall back to text parsing
                log.warning("structured RCA output failed; falling back to text parse", exc_info=True)
                report = None
        if report is None:
            try:
                resp = llm.invoke(msgs)
                # Accumulate, never overwrite: if the structured call above already
                # spent tokens (HTTP-ok but parse failed), they must still count.
                tokens += _log_usage("report", resp)
                report = _parse_report(str(resp.content), fallback_summary=final_answer)
            except Exception:  # noqa: BLE001 — reporting must never break a finished run
                log.exception("report synthesis failed; using fallback report")
                report = _parse_report("", fallback_summary=final_answer)
        report = _calibrate_confidence(report)
        report = _verify_grounding(report, digest)  # deterministic critic pass
        # Adversarial critique (LLM): prosecute → defend → judge, then fold the
        # verdict into confidence. Gated to replay-off (two extra .invoke calls the
        # cassette layer doesn't wrap) and to reports that actually name a cause.
        if settings.copilot_adversarial_critique and replay.mode() == "off" and report.get("root_cause"):
            critique = _adversarial_critique(llm, request, report, digest)
            tokens += critique.get("tokens", 0)
            report = _apply_critique(report, critique)
        report["postmortem"] = _render_postmortem(report, request)
        return {"report": report, "status": "done", "tokens_used": tokens}

    return report_node


# --------------------------------------------------------------------------- #
# Verify node — assess whether the PROPOSED FIX resolves the incident.
# The report node answers "what's the cause + here's a suggested fix"; this node
# closes the loop: does that fix actually address the root cause, and what signal
# would confirm resolution? It grounds the LLM's verdict deterministically (a fix
# that doesn't touch the implicated code can't be "verified"), and — bounded by
# copilot_verify_max_attempts — bounces the run back to the agent to revise a fix
# that misses. All parsing/grounding lives in pure helpers so it's unit-testable
# without an LLM, and it degrades gracefully: verification can never break a run.
# --------------------------------------------------------------------------- #
_VERIFY_VERDICTS = {"verified", "unverified", "inconclusive", "no_fix_proposed"}


def _extract_proposed_fix(state: AgentState, report: dict) -> dict:
    """Find the remediation the run proposed, for the verify node to assess. Prefers
    a create_pull_request tool call (the strongest 'a fix was proposed' signal); else
    falls back to the report's recommended actions when a root cause was established.
    Pure + testable. Returns {has_fix, source, text}."""
    pr_parts: list[str] = []
    patch = ""
    for m in state.get("messages", []):
        if isinstance(m, AIMessage) and m.tool_calls:
            for c in m.tool_calls:
                if c.get("name") != "create_pull_request":
                    continue
                args = c.get("args") or {}
                # A machine-applicable patch (unified diff) is the strongest fix
                # artifact — it's what the sandbox counterfactual actually runs.
                if args.get("patch") and isinstance(args["patch"], str):
                    patch = args["patch"]
                for k in ("title", "body", "description", "diff", "patch", "files", "branch", "head"):
                    v = args.get(k)
                    if v:
                        pr_parts.append(f"{k}: {v if isinstance(v, str) else json.dumps(v, default=str)}")
    actions = report.get("recommended_actions") or []
    if pr_parts:
        text = "\n".join(pr_parts)
        if actions:
            text += "\nRecommended actions:\n" + "\n".join(actions)
        return {"has_fix": True, "source": "pr", "text": text[:2500], "patch": patch}
    if report.get("root_cause") and actions:
        return {"has_fix": True, "source": "actions", "text": "\n".join(actions)[:2000], "patch": ""}
    return {"has_fix": False, "source": "none", "text": "", "patch": ""}


def _fix_targets_cause(fix_text: str, report: dict) -> dict:
    """Deterministic check that the proposed fix references the files/services/symbols
    implicated by the root cause — a fix that touches unrelated code can't be
    'verified'. Pure + free (no LLM). Returns {grounded, shared, ratio}."""
    cause_blob = " ".join(
        [
            report.get("root_cause") or "",
            " ".join(report.get("affected_services") or []),
            " ".join(report.get("evidence") or []),
            " ".join(ev for h in (report.get("hypotheses") or []) for ev in (h.get("evidence") or [])),
        ]
    )
    cause_tokens = _salient_tokens(cause_blob)
    fix_tokens = _salient_tokens(fix_text)
    if not cause_tokens or not fix_tokens:
        return {"grounded": False, "shared": [], "ratio": None}
    shared = fix_tokens & cause_tokens
    # A shared token that looks like a file/path/symbol/identifier (has a separator
    # or a digit) is strong evidence the fix targets the implicated code, not just
    # incidental English overlap.
    specific = {t for t in shared if any(c in t for c in "._/:") or any(ch.isdigit() for ch in t)}
    grounded = len(shared) >= 2 or bool(specific)
    return {"grounded": grounded, "shared": sorted(shared)[:8], "ratio": round(len(shared) / len(cause_tokens), 2)}


def _normalize_verification(data: dict, has_fix: bool) -> dict:
    """Coerce a parsed model dict into the canonical verification shape with safe
    defaults. Pure + total: any odd input yields a valid verification, not a raise."""
    verdict = str(data.get("verdict", "")).strip().lower()
    if verdict not in _VERIFY_VERDICTS:
        verdict = "inconclusive" if has_fix else "no_fix_proposed"
    conf = str(data.get("confidence", "")).strip().lower()
    rationale = data.get("rationale")
    return {
        "verdict": verdict,
        "addresses_cause": bool(data.get("addresses_cause")),
        "confidence": conf if conf in _CONFIDENCE else "low",
        "resolution_criteria": _coerce_str_list(data.get("resolution_criteria"), limit=8),
        "residual_risks": _coerce_str_list(data.get("residual_risks"), limit=8),
        "rationale": rationale.strip()[:600] if isinstance(rationale, str) else "",
    }


def _parse_verification(text: str, has_fix: bool) -> dict:
    """Extract the JSON object from a model reply and normalize it. On any failure,
    return a safe inconclusive (or no_fix_proposed) verification."""
    fallback = _normalize_verification({}, has_fix)
    if not text or not text.strip():
        return fallback
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end <= start:
        return fallback
    try:
        data = json.loads(text[start : end + 1])
    except (ValueError, TypeError):
        return fallback
    if not isinstance(data, dict):
        return fallback
    return _normalize_verification(data, has_fix)


def _maybe_learn(request: str, report: dict) -> None:
    """Best-effort: record a confidently-resolved investigation as a reusable runbook
    in the incident-memory corpus, so future similar incidents warm-start from it.
    Never fatal to a finished run."""
    try:
        rec = incident_memory.learn_from_report(request, report, date=time.strftime("%Y-%m-%d"))
        if rec:
            log.info("learned incident into corpus: %s", rec.get("id"))
    except Exception:  # noqa: BLE001 — learning must never break a finished run
        log.warning("incident learning failed (non-fatal)", exc_info=True)


def make_verify_node():
    """Node: verify that the proposed fix would resolve the incident. Runs once after
    the report node. Emits report["verification"] and, when a fix clearly misses the
    root cause, bounces back to the agent (bounded) to revise it."""
    settings = get_settings()
    llm = get_llm(fast=True)

    def verify_node(state: AgentState) -> dict:
        report = state.get("report") or {}
        request = _last_user_text(state)
        # Feature disabled -> behave as before (report was terminal), but still
        # capture the resolved incident into memory.
        if not settings.copilot_verify_fix:
            _maybe_learn(request, report)
            return {"status": "done"}

        fix = _extract_proposed_fix(state, report)
        if not fix["has_fix"]:
            # Nothing to verify (informational request, or cause explained without a
            # remediation). Annotate and finish — never loop.
            verification = _normalize_verification(
                {"verdict": "no_fix_proposed", "rationale": "No remediation was proposed to verify."},
                has_fix=False,
            )
            report["verification"] = verification
            report["postmortem"] = _render_postmortem(report, request)
            _maybe_learn(request, report)
            return {"verification": verification, "report": report, "status": "done"}

        digest = _evidence_digest(state)
        human = (
            f"Root cause:\n{report.get('root_cause') or '(none explicitly stated)'}\n\n"
            f"Proposed fix:\n{fix['text']}\n\n"
            f"Evidence gathered (tool calls + results):\n{digest or '(none recorded)'}"
        )
        tokens = 0
        try:
            resp = llm.invoke([SystemMessage(content=VERIFY_SYSTEM), HumanMessage(content=human)])
            tokens = _log_usage("verify", resp)
            verification = _parse_verification(str(resp.content), has_fix=True)
        except Exception:  # noqa: BLE001 — verification must never break a finished run
            log.exception("fix verification failed; using inconclusive fallback")
            verification = _parse_verification("", has_fix=True)

        # Deterministic reconciliation: the LLM can't self-certify a fix that doesn't
        # touch the code/service implicated by the root cause. Downgrade if so.
        grounding = _fix_targets_cause(fix["text"], report)
        verification["grounding"] = grounding
        if verification["verdict"] == "verified" and not grounding["grounded"]:
            verification["verdict"] = "inconclusive"
            verification["confidence"] = "low"
            verification["rationale"] = (
                "[downgraded: the proposed fix does not reference the files/services "
                "implicated by the root cause] " + verification.get("rationale", "")
            ).strip()

        # Sandbox counterfactual: if enabled and the agent attached a patch, PROVE the
        # fix by applying it to a throwaway repo copy and running a reproducer. A
        # hard FAIL→PASS result overrides the LLM's opinion in either direction.
        if settings.copilot_sandbox_verify and fix.get("patch"):
            sandbox = run_counterfactual(
                settings.repo_path,
                fix["patch"],
                settings.copilot_sandbox_cmd,
                settings.copilot_sandbox_timeout_s,
            )
            verification["sandbox"] = sandbox
            if sandbox["verdict"] == "resolved":
                verification["verdict"] = "verified"
                verification["confidence"] = "high"
                verification["rationale"] = (
                    "[sandbox-proven: reproducer failed before the patch and passes after it] "
                    + verification.get("rationale", "")
                ).strip()
            elif sandbox["verdict"] == "not_resolved":
                verification["verdict"] = "unverified"
                verification["rationale"] = (
                    "[sandbox-disproven: reproducer still fails after applying the patch] "
                    + verification.get("rationale", "")
                ).strip()

        report["verification"] = verification
        report["postmortem"] = _render_postmortem(report, request)

        # Bounded loop-back: an unverified fix gets ONE revision pass (guarded by the
        # attempt cap AND the token budget) so the loop can never spin.
        attempts = state.get("verify_attempts", 0)
        can_retry = (
            verification["verdict"] == "unverified"
            and attempts < settings.copilot_verify_max_attempts
            and not _over_token_budget(state.get("tokens_used", 0), settings)
        )
        if can_retry:
            criteria = "; ".join(verification.get("resolution_criteria") or []) or (
                "the incident's failing signal returns to normal"
            )
            feedback = (
                "Your proposed fix does not resolve the root cause. "
                f"{verification.get('rationale', '')} "
                f"Revise the fix so it directly addresses the root cause and would satisfy: {criteria}."
            ).strip()
            return {
                "verification": verification,
                "report": report,
                "feedback": feedback,
                "verify_attempts": 1,
                "status": "investigating",
                "tokens_used": tokens,
            }
        _maybe_learn(request, report)
        return {"verification": verification, "report": report, "status": "done", "tokens_used": tokens}

    return verify_node
