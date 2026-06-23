"""High-level agent session used by both the CLI and the API.

Owns the lifecycle: start MCP servers, build the graph, run a turn, and surface
human-in-the-loop interrupts so the caller can approve/reject write actions.
"""

from __future__ import annotations

from contextlib import AsyncExitStack
from dataclasses import dataclass, field
from typing import Any

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langgraph.types import Command

from app.graph.builder import build_graph, make_checkpointer
from app.mcp.client import load_mcp_tools


@dataclass
class TurnResult:
    """Outcome of one run/resume call."""

    status: str  # "completed" | "awaiting_approval"
    final_text: str = ""
    approval_request: dict | None = None
    trace: list[str] = field(default_factory=list)


class CopilotSession:
    """A live agent session. One session ≈ one conversation thread."""

    def __init__(self, thread_id: str = "default"):
        self.thread_id = thread_id
        self._stack = AsyncExitStack()
        self._graph = None
        self._mcp_client = None

    async def __aenter__(self) -> "CopilotSession":
        tools, self._mcp_client = await load_mcp_tools()
        checkpointer = await self._stack.enter_async_context(make_checkpointer())
        self._graph = build_graph(tools, checkpointer)
        return self

    async def __aexit__(self, *exc) -> None:
        await self._stack.aclose()

    @property
    def _config(self) -> dict:
        return {"configurable": {"thread_id": self.thread_id}}

    async def ask(self, question: str) -> TurnResult:
        """Start a new investigation turn."""
        return await self._drive({"messages": [HumanMessage(content=question)]})

    async def resume(self, approved: bool, reason: str = "") -> TurnResult:
        """Resume a turn that paused for human approval."""
        decision = {"approved": approved, "reason": reason}
        return await self._drive(Command(resume=decision))

    async def _drive(self, graph_input: Any) -> TurnResult:
        """Stream the graph until it finishes or hits an approval interrupt."""
        trace: list[str] = []
        async for event in self._graph.astream(
            graph_input, config=self._config, stream_mode="updates"
        ):
            for node, update in event.items():
                trace.append(_describe(node, update))
                # An interrupt surfaces as a special "__interrupt__" key.
                if node == "__interrupt__":
                    payload = update[0].value if isinstance(update, tuple) else update
                    return TurnResult(
                        status="awaiting_approval",
                        approval_request=payload,
                        trace=trace,
                    )

        # Finished: pull the final assistant text from persisted state.
        snapshot = await self._graph.aget_state(self._config)
        final_text = _last_ai_text(snapshot.values.get("messages", []))
        return TurnResult(status="completed", final_text=final_text, trace=trace)


def _describe(node: str, update: Any) -> str:
    """Render a single graph step for the live trace shown to the user."""
    if node == "plan":
        steps = (update or {}).get("plan", [])
        return f"📋 planned {len(steps)} step(s)"
    if node == "agent":
        msgs = (update or {}).get("messages", [])
        for m in msgs:
            if isinstance(m, AIMessage) and m.tool_calls:
                names = ", ".join(c["name"] for c in m.tool_calls)
                return f"🤖 calling tool(s): {names}"
        return "🤖 reasoning"
    if node == "tools":
        msgs = (update or {}).get("messages", [])
        names = ", ".join(getattr(m, "name", "?") for m in msgs if isinstance(m, ToolMessage))
        return f"🔧 tool result: {names}"
    if node == "approval":
        return "⏸️  awaiting human approval"
    if node == "reflect":
        return f"🔁 reflect -> {(update or {}).get('status', '?')}"
    if node == "__interrupt__":
        return "⏸️  interrupted for approval"
    return f"• {node}"


def _last_ai_text(messages: list) -> str:
    for msg in reversed(messages):
        if isinstance(msg, AIMessage) and not msg.tool_calls:
            return msg.content if isinstance(msg.content, str) else str(msg.content)
    return "(no final answer produced)"
