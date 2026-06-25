"""Graph nodes: plan, agent (reason/act), approval (human-in-the-loop), reflect.

Tool *execution* is handled by a prebuilt ToolNode wired up in builder.py; these
nodes provide the reasoning and control logic around it.
"""

from __future__ import annotations

import json
import logging

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from langgraph.types import interrupt

from app.config import get_settings
from app.graph.prompts import AGENT_SYSTEM, PLANNER_SYSTEM, REFLECT_SYSTEM, REPORT_SYSTEM
from app.graph.state import AgentState
from app.llm import get_llm
from app.mcp.client import WRITE_TOOLS

log = logging.getLogger("devcopilot.agent")


def _log_usage(node: str, resp) -> None:
    """Log per-call LLM token usage so cost is observable (carries the request-id
    via the logging filter). usage_metadata is absent on some providers — skip then."""
    um = getattr(resp, "usage_metadata", None)
    if not um:
        return
    details = um.get("input_token_details") or {}
    log.info(
        "llm_usage node=%s input=%s output=%s total=%s cache_read=%s",
        node,
        um.get("input_tokens"),
        um.get("output_tokens"),
        um.get("total_tokens"),
        details.get("cache_read"),
    )


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
        human = f"Conversation so far:\n{digest}\n\nNew request:\n{request}" if digest else request
        resp = llm.invoke(
            [SystemMessage(content=PLANNER_SYSTEM), HumanMessage(content=human)]
        )
        _log_usage("plan", resp)
        plan = [ln.strip() for ln in str(resp.content).splitlines() if ln.strip()]
        return {"plan": plan, "iteration": 0, "status": "investigating", "feedback": ""}

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
    llm_tools = get_llm().bind_tools(tools)
    llm_plain = get_llm()  # no tools — used to force a final answer at the cap

    def agent_node(state: AgentState) -> dict:
        iteration = state.get("iteration", 0) + 1
        plan_text = "\n".join(state.get("plan", [])) or "(no explicit plan)"
        feedback = state.get("feedback", "")
        at_cap = iteration >= settings.copilot_max_iterations

        if at_cap:
            system = SystemMessage(
                content=AGENT_SYSTEM.format(plan=plan_text)
                + "\n\nYou have reached the investigation step limit. Do NOT call "
                "any tools. Summarize the root cause and your recommendation now."
            )
            resp = llm_plain.invoke([system, *state["messages"]])
        else:
            base = AGENT_SYSTEM.format(plan=plan_text)
            if feedback:
                # The reflect node judged the last answer incomplete — tell the
                # agent exactly what to close so it doesn't repeat itself.
                base += (
                    "\n\nReviewer feedback on your previous answer — address this "
                    f"now before finishing:\n{feedback}"
                )
            resp = llm_tools.invoke([SystemMessage(content=base), *state["messages"]])

        _log_usage("agent", resp)
        return {"messages": [resp], "iteration": iteration}

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
    # message on approval), tagging which ones mutate state — so the reviewer
    # isn't shown only the write and silently approving co-bundled reads too.
    decision = interrupt(
        {
            "type": "approval_request",
            "message": "The agent wants to run an action that includes a write operation.",
            "actions": [
                {"tool": c["name"], "args": c["args"], "write": c["name"] in WRITE_TOOLS}
                for c in all_calls
            ],
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
        # `iteration` is incremented in agent_node; reflect only reads it.
        if state.get("iteration", 0) >= settings.copilot_max_iterations:
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
        _log_usage("reflect", resp)
        text = str(resp.content).strip()
        if text.upper().startswith("DONE"):
            return {"status": "done", "feedback": ""}
        # CONTINUE — capture the gap note (everything after the verdict word) so
        # the next agent pass closes it instead of re-emitting the same answer.
        rest = text[len("CONTINUE"):] if text.upper().startswith("CONTINUE") else text
        feedback = rest.lstrip(" :\n-").strip()[:500]
        return {"status": "investigating", "feedback": feedback}

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


def _render_postmortem(report: dict, request: str) -> str:
    """Render a blameless, copy-pasteable postmortem from the RCA object. Pure:
    deterministic Markdown, no extra LLM call. Roles/actions, never names."""
    rc = report.get("root_cause") or "Inconclusive — see hypotheses below."
    services = ", ".join(report.get("affected_services") or []) or "—"
    lines = [
        "# Incident Postmortem",
        "",
        f"**Severity:** {report.get('severity', 'SEV3')}  ·  "
        f"**Confidence:** {report.get('confidence', 'low')}  ·  "
        f"**Affected services:** {services}",
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
    lines += [
        "",
        "---",
        "_Generated by DevOps Copilot. Blameless by design — this describes systems "
        "and actions, not individuals. Review before sharing._",
    ]
    return "\n".join(lines)


def make_report_node():
    """Node: compile the finished investigation into a structured RCA report +
    postmortem. Runs once when the investigation is done (after reflect)."""
    # Use the fast model: it's a synthesis-from-given-text task (cheap), and it
    # avoids the adaptive-thinking + forced-tool conflict on the main model.
    llm = get_llm(fast=True)

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
        try:
            resp = llm.invoke([SystemMessage(content=REPORT_SYSTEM), HumanMessage(content=human)])
            _log_usage("report", resp)
            report = _parse_report(str(resp.content), fallback_summary=final_answer)
        except Exception:  # noqa: BLE001 — reporting must never break a finished run
            log.exception("report synthesis failed; using fallback report")
            report = _parse_report("", fallback_summary=final_answer)
        report["postmortem"] = _render_postmortem(report, request)
        return {"report": report, "status": "done"}

    return report_node
