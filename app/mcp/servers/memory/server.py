"""Incident-memory MCP server — "have we seen this before?"

Institutional memory is the biggest accuracy lever for recurring incidents: the
first move on a fresh page should be to search prior incidents, RCAs, and runbooks
for a match. This server exposes that search to the agent. The retrieval logic
(BM25, fully offline, no embeddings model or network) lives in `app.incident_memory`
so the planner can use the same code to warm-start an investigation.

Pure vector similarity is risky for ops knowledge (it happily confuses an "enable X"
runbook with a "disable X" one), so lexical grounding is a feature here.

Tools:
  - search_incidents:   rank prior incidents by similarity to a query.
  - get_incident_record: fetch one prior incident's full record (incl. runbook).

Run standalone:
    python -m app.mcp.servers.memory.server
"""

from __future__ import annotations

import os

from mcp.server.fastmcp import FastMCP

from app import incident_memory

# Honor CORPUS_PATH (set via the MCP client from COPILOT incident_corpus_path); blank
# falls back to the bundled demo corpus inside app.incident_memory.
CORPUS_PATH = os.environ.get("CORPUS_PATH", "").strip()

mcp = FastMCP("memory")

# Re-exported so callers/tests can use the pure ranker directly.
bm25_rank = incident_memory.bm25_rank


@mcp.tool()
def search_incidents(query: str, limit: int | str = 3) -> list[dict]:
    """Search prior incidents/RCAs/runbooks for ones similar to `query` (e.g. the
    current symptom: "checkout-svc 5xx TypeError applyDiscount"). Returns the top
    matches with title, root cause, resolution, runbook, and a relevance score —
    so the agent can reuse a known fix instead of re-deriving it.
    """
    try:
        limit = int(limit)
    except (TypeError, ValueError):
        limit = 3
    return incident_memory.search(query, limit=limit, corpus=CORPUS_PATH)


@mcp.tool()
def get_incident_record(incident_id: str) -> dict:
    """Fetch one prior incident's full record by id (including its runbook)."""
    return incident_memory.get_record(incident_id, corpus=CORPUS_PATH)


if __name__ == "__main__":
    mcp.run(transport="stdio")
