"""Conditional routing functions for the graph."""

from __future__ import annotations

from typing import Literal

from app import policy
from app.graph.state import AgentState


def route_after_agent(state: AgentState) -> Literal["approval", "tools", "reflect"]:
    """Decide what happens after the agent speaks.

    - tool calls that include a WRITE  -> human approval first (never bypassed)
    - tool calls that are all reads     -> execute immediately
    - no tool calls (a final answer)    -> reflect on completeness

    The iteration cap is enforced in agent_node (it stops calling tools at the
    cap), so tool_calls here are always meant to run — never strand them.
    """
    last = state["messages"][-1]
    tool_calls = getattr(last, "tool_calls", None)
    if not tool_calls:
        return "reflect"
    # The policy engine is argument-aware (e.g. scale-to-zero escalates to approve),
    # so classify each call's args, not just its name.
    if any(policy.requires_approval(call["name"], call.get("args")) for call in tool_calls):
        return "approval"
    return "tools"


def route_after_approval(state: AgentState) -> Literal["tools", "agent"]:
    """After the human decides:

    - approved  -> the original AIMessage with tool_calls is intact -> execute
    - rejected  -> we appended ToolMessages, so hand back to the agent
    """
    last = state["messages"][-1]
    # If the last message is an AIMessage still holding tool_calls, it was approved.
    if getattr(last, "tool_calls", None):
        return "tools"
    return "agent"


def route_after_reflect(state: AgentState) -> Literal["agent", "report"]:
    """When done, compile the structured RCA report; otherwise keep investigating."""
    return "report" if state.get("status") == "done" else "agent"
