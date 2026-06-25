"""Graph nodes: plan, agent (reason/act), approval (human-in-the-loop), reflect.

Tool *execution* is handled by a prebuilt ToolNode wired up in builder.py; these
nodes provide the reasoning and control logic around it.
"""

from __future__ import annotations

import logging

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from langgraph.types import interrupt

from app.config import get_settings
from app.graph.prompts import AGENT_SYSTEM, PLANNER_SYSTEM, REFLECT_SYSTEM
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
