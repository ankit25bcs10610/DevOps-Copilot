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

from contextlib import asynccontextmanager

from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from langgraph.graph import END, START, StateGraph
from langgraph.prebuilt import ToolNode

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


def build_graph(tools, checkpointer):
    """Compile the graph given the loaded MCP tools and a checkpointer."""
    g = StateGraph(AgentState)

    g.add_node("plan", make_plan_node())
    g.add_node("agent", make_agent_node(tools))
    g.add_node("tools", ToolNode(tools))  # executes read tools + approved writes
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
