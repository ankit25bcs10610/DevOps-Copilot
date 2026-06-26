r"""Assemble the LangGraph state machine.

Wiring:

    START -> plan -> agent --(route_after_agent)--> approval / tools / reflect
                       ^                                  |        |       |
                       |        approved -> tools <-------+        |       |
                       |        rejected -> agent                  |       |
                       +------------------ tools --(back to)-- agent       |
                       +-------------- reflect --(continue)-- agent        |
                                              \--(done)--> END  <----------+

The checkpointer persists state per `thread_id`, which is what makes the
human-in-the-loop interrupt resumable across separate API calls.
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from langchain_core.messages import ToolMessage
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from langgraph.graph import END, START, StateGraph
from langgraph.prebuilt import ToolNode

from app import audit, guardrails, redaction
from app.config import get_settings
from app.graph.edges import route_after_agent, route_after_approval, route_after_reflect
from app.graph.nodes import (
    approval_node,
    make_agent_node,
    make_plan_node,
    make_reflect_node,
    make_report_node,
)
from app.graph.state import AgentState

log = logging.getLogger("devcopilot.guardrails")


def make_guarded_tool_node(tools):
    """ToolNode wrapped with prompt-injection defenses: every tool result is
    provenance-boxed and scanned for injection patterns before it re-enters the
    model's context, and any hit is audited. The agent therefore never sees raw
    untrusted telemetry — only labeled, defanged data."""
    inner = ToolNode(tools)

    async def guarded(state: AgentState) -> dict:
        result = await inner.ainvoke(state)
        out: list = []
        for m in result.get("messages", []):
            if isinstance(m, ToolMessage):
                raw = m.content if isinstance(m.content, str) else str(m.content)
                # 1) Redact PII/secrets BEFORE the model sees it or state persists it.
                redacted, entities = redaction.scrub(raw)
                if entities:
                    audit.record("telemetry.redacted", tool=m.name, count=len(entities),
                                 types=sorted({e["type"] for e in entities}))
                # 2) Provenance-box + injection-scan the (already redacted) output.
                clean, flags = guardrails.sanitize_tool_output(m.name or "tool", redacted)
                if flags:
                    log.warning("prompt-injection patterns in %s output: %s", m.name, flags)
                    audit.record("security.prompt_injection_detected", tool=m.name, patterns=flags)
                out.append(
                    ToolMessage(content=clean, tool_call_id=m.tool_call_id, name=m.name)
                )
            else:
                out.append(m)
        return {"messages": out}

    return guarded


def build_graph(tools, checkpointer):
    """Compile the graph given the loaded MCP tools and a checkpointer."""
    g = StateGraph(AgentState)

    g.add_node("plan", make_plan_node())
    g.add_node("agent", make_agent_node(tools))
    # Executes read tools + approved writes, then provenance-boxes & injection-scans
    # every result before it re-enters the agent's context.
    g.add_node("tools", make_guarded_tool_node(tools))
    g.add_node("approval", approval_node)
    g.add_node("reflect", make_reflect_node())
    g.add_node("report", make_report_node())  # compile the structured RCA deliverable

    g.add_edge(START, "plan")
    g.add_edge("plan", "agent")

    g.add_conditional_edges(
        "agent",
        route_after_agent,
        {"approval": "approval", "tools": "tools", "reflect": "reflect"},
    )
    g.add_conditional_edges(
        "approval",
        route_after_approval,
        {"tools": "tools", "agent": "agent"},
    )
    g.add_edge("tools", "agent")
    g.add_conditional_edges(
        "reflect",
        route_after_reflect,
        {"agent": "agent", "report": "report"},
    )
    g.add_edge("report", END)

    return g.compile(checkpointer=checkpointer)


def make_checkpointer():
    """Return an async checkpointer context manager.

    Defaults to SQLite (single-instance). If COPILOT_CHECKPOINT_DB is a Postgres
    URL, uses the Postgres saver instead — the swap that enables multi-instance
    deployments, since graph state is already keyed by thread_id. Postgres needs
    the optional `langgraph-checkpoint-postgres` package.
    """
    db = get_settings().copilot_checkpoint_db
    if db.startswith(("postgres://", "postgresql://")):
        return _postgres_checkpointer(db)
    return AsyncSqliteSaver.from_conn_string(db)


@asynccontextmanager
async def _postgres_checkpointer(conn_string: str):
    try:
        from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
    except ImportError as exc:  # noqa: TRY003
        raise RuntimeError(
            "Postgres checkpointer requires 'langgraph-checkpoint-postgres' "
            "(pip install langgraph-checkpoint-postgres)."
        ) from exc
    async with AsyncPostgresSaver.from_conn_string(conn_string) as saver:
        await saver.setup()  # idempotent: ensures the checkpoint tables exist
        yield saver
