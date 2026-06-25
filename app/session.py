"""High-level agent session used by both the CLI and the API.

Owns the lifecycle: start MCP servers, build the graph, run a turn, and surface
human-in-the-loop interrupts so the caller can approve/reject write actions.
"""

from __future__ import annotations

from contextlib import AsyncExitStack
from dataclasses import dataclass, field
from typing import Any, AsyncIterator

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langgraph.errors import GraphRecursionError
from langgraph.types import Command

from app.config import get_settings
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
        try:
            # Persistent MCP sessions live in self._stack for the session lifetime.
            tools, self._mcp_client = await load_mcp_tools(self._stack)
            checkpointer = await self._stack.enter_async_context(make_checkpointer())
            self._graph = build_graph(tools, checkpointer)
            return self
        except BaseException:
            # Don't leak half-started MCP subprocesses if init fails partway.
            await self._stack.aclose()
            raise

    async def __aexit__(self, *exc) -> None:
        await self._stack.aclose()

    @property
    def _config(self) -> dict:
        # recursion_limit must exceed the worst-case super-step count of the
        # plan -> agent -> (tools -> agent)* -> reflect loop. Derive it from the
        # agent's own iteration cap so raising COPILOT_MAX_ITERATIONS can never
        # let the graph crash (GraphRecursionError) before the agent voluntarily
        # finishes at the cap. The default LangGraph limit of 25 is too low once
        # approval round-trips and reflect retries are counted.
        cap = get_settings().copilot_max_iterations
        return {
            "configurable": {"thread_id": self.thread_id},
            "recursion_limit": cap * 4 + 10,
        }

    async def pending_interrupt(self) -> bool:
        """True if this thread is paused mid-run (awaiting human approval).

        After a normal completion the graph's `next` is empty; after an
        `interrupt()` it holds the paused node. Used to (a) reject a fresh /chat
        on a paused thread and (b) tell a reconstructed session whether there's
        actually something to resume.
        """
        snapshot = await self._graph.aget_state(self._config)
        return bool(getattr(snapshot, "next", ()))

    async def ask(self, question: str) -> TurnResult:
        """Start a new investigation turn (non-streaming; used by CLI/evals)."""
        return await self._drive({"messages": [HumanMessage(content=question)]})

    async def resume(self, approved: bool, reason: str = "") -> TurnResult:
        """Resume a turn that paused for human approval (non-streaming)."""
        return await self._drive(Command(resume={"approved": approved, "reason": reason}))

    async def ask_stream(self, question: str) -> AsyncIterator[dict]:
        """Like ask(), but yields progress events as they happen (for SSE)."""
        async for ev in self._drive_events({"messages": [HumanMessage(content=question)]}):
            yield ev

    async def resume_stream(self, approved: bool, reason: str = "") -> AsyncIterator[dict]:
        """Like resume(), but yields progress events as they happen (for SSE)."""
        decision = Command(resume={"approved": approved, "reason": reason})
        async for ev in self._drive_events(decision):
            yield ev

    async def _drive_events(self, graph_input: Any) -> AsyncIterator[dict]:
        """Stream the graph as a sequence of events:
          {"type": "trace", "node", "line"}            — one per graph step
          {"type": "approval", "approval_request", "trace"}  — paused for a human
          {"type": "done", "final_text", "trace"}      — finished
        """
        trace: list[str] = []
        try:
            async for event in self._graph.astream(
                graph_input, config=self._config, stream_mode="updates"
            ):
                for node, update in event.items():
                    line = _describe(node, update)
                    trace.append(line)
                    # An interrupt surfaces as a special "__interrupt__" key.
                    if node == "__interrupt__":
                        payload = update[0].value if isinstance(update, tuple) else update
                        yield {
                            "type": "approval",
                            "approval_request": payload,
                            "trace": list(trace),
                        }
                        return
                    yield {"type": "trace", "node": node, "line": line}
        except GraphRecursionError:
            # Safety net: the agent's iteration cap should normally end the run,
            # but reflect/approval round-trips can still hit the graph limit.
            # Surface a clean message instead of a 500.
            yield {
                "type": "done",
                "final_text": (
                    "I reached the maximum number of investigation steps before "
                    "concluding. Try narrowing the question, or raise "
                    "COPILOT_MAX_ITERATIONS. The trace above shows how far I got."
                ),
                "trace": list(trace),
            }
            return

        # Finished: pull the final assistant text from persisted state.
        snapshot = await self._graph.aget_state(self._config)
        final_text = _last_ai_text(snapshot.values.get("messages", []))
        yield {"type": "done", "final_text": final_text, "trace": list(trace)}

    async def _drive(self, graph_input: Any) -> TurnResult:
        """Run a turn to completion, collapsing the event stream into one result."""
        last: dict | None = None
        async for ev in self._drive_events(graph_input):
            if ev["type"] in ("approval", "done"):
                last = ev
        if last and last["type"] == "approval":
            return TurnResult(
                status="awaiting_approval",
                approval_request=last["approval_request"],
                trace=last["trace"],
            )
        if last and last["type"] == "done":
            return TurnResult(status="completed", final_text=last["final_text"], trace=last["trace"])
        return TurnResult(status="completed", final_text="(no final answer produced)")


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
    # Prefer the last tool-call-free assistant message (a real final answer).
    for msg in reversed(messages):
        if isinstance(msg, AIMessage) and not msg.tool_calls:
            text = msg.content if isinstance(msg.content, str) else str(msg.content)
            if text.strip():
                return text
    # Fallback: last assistant message with any text (even if it had tool_calls),
    # so a run that ended on a tool-calling message still surfaces something.
    for msg in reversed(messages):
        if isinstance(msg, AIMessage) and msg.content:
            return msg.content if isinstance(msg.content, str) else str(msg.content)
    return "(no final answer produced)"
