"""The agent's shared state — the object that flows through every graph node."""

from __future__ import annotations

from typing import Annotated, Literal, TypedDict

from langgraph.graph.message import add_messages

Status = Literal["investigating", "awaiting_approval", "done", "failed"]


class AgentState(TypedDict, total=False):
    # Full conversation incl. tool calls/results. `add_messages` appends and
    # de-duplicates by id, so each node just returns the new messages.
    messages: Annotated[list, add_messages]

    # The investigation plan produced by the planner.
    plan: list[str]

    # A write tool call waiting for human approval (None when nothing pending).
    pending_action: dict | None

    # Loop guard so the agent can't spin forever.
    iteration: int

    # Lifecycle marker, used by the API/CLI to know when to stop.
    status: Status
