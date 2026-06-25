"""Incident-memory MCP server — "have we seen this before?"

Institutional memory is the biggest accuracy lever for recurring incidents: the
first move on a fresh page should be to search prior incidents, RCAs, and runbooks
for a match. This server does that over a bundled corpus using BM25 ranking
(keyword + IDF + length-normalized term frequency) — a hybrid-leaning, fully
offline retriever that needs no embeddings model or network, so the single-artifact
constraint holds. Pure vector similarity is risky for ops knowledge (it happily
confuses an "enable X" runbook with a "disable X" one), so lexical grounding is a
feature, not a limitation, here.

To extend: point CORPUS_PATH at your own JSON corpus of postmortems/runbooks; the
RCA report node's output is exactly the shape worth appending after each incident,
closing the learning loop.

Tools:
  - search_incidents:   rank prior incidents by similarity to a query.
  - get_incident_record: fetch one prior incident's full record (incl. runbook).

Run standalone:
    python -m app.mcp.servers.memory.server
"""

from __future__ import annotations

import json
import math
import os
import re
from collections import Counter
from pathlib import Path

from mcp.server.fastmcp import FastMCP

CORPUS_PATH = Path(
    os.environ.get("CORPUS_PATH", str(Path(__file__).resolve().parent / "corpus.json"))
).resolve()

mcp = FastMCP("memory")


def _load_corpus() -> list[dict]:
    try:
        return json.loads(CORPUS_PATH.read_text())
    except (OSError, ValueError):
        return []


def _tokenize(text: str) -> list[str]:
    return re.findall(r"[a-z0-9]+", (text or "").lower())


def _doc_text(rec: dict) -> str:
    """The searchable text for a record (title + summary + root cause + tags)."""
    return " ".join([
        rec.get("title", ""), rec.get("summary", ""), rec.get("root_cause", ""),
        rec.get("service", ""), " ".join(rec.get("tags", [])),
    ])


def bm25_rank(query: str, records: list[dict], k1: float = 1.5, b: float = 0.75) -> list[dict]:
    """Rank records against a query with BM25. Pure + deterministic so it's
    unit-testable. Returns records annotated with a `score` (best first)."""
    docs = [_tokenize(_doc_text(r)) for r in records]
    n = len(docs)
    if n == 0:
        return []
    df: Counter[str] = Counter()
    for d in docs:
        for t in set(d):
            df[t] += 1
    idf = {t: math.log(1 + (n - c + 0.5) / (c + 0.5)) for t, c in df.items()}
    avgdl = sum(len(d) for d in docs) / n or 1.0
    q_tokens = _tokenize(query)

    scored = []
    for rec, doc in zip(records, docs):
        tf = Counter(doc)
        dl = len(doc)
        score = 0.0
        for t in q_tokens:
            f = tf.get(t, 0)
            if not f or t not in idf:
                continue
            score += idf[t] * (f * (k1 + 1)) / (f + k1 * (1 - b + b * dl / avgdl))
        scored.append((score, rec))
    scored.sort(key=lambda sr: sr[0], reverse=True)
    return [{**rec, "score": round(score, 3)} for score, rec in scored]


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
    ranked = bm25_rank(query, _load_corpus())
    # Drop zero-score (no lexical overlap) results so a miss returns nothing,
    # not an arbitrary "closest" record.
    return [r for r in ranked if r["score"] > 0][:limit]


@mcp.tool()
def get_incident_record(incident_id: str) -> dict:
    """Fetch one prior incident's full record by id (including its runbook)."""
    for rec in _load_corpus():
        if rec.get("id") == incident_id:
            return rec
    return {"error": f"no prior incident '{incident_id}' in the corpus"}


if __name__ == "__main__":
    mcp.run(transport="stdio")
