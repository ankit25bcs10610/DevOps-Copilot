"""Incident-memory search — shared BM25 retrieval over a corpus of prior incidents.

The pure retrieval logic lives here so it can be used in two places without a
subprocess hop: the `memory` MCP server (exposes it as a tool to the agent) and
the planner (warm-starts the investigation with known recurring failure modes —
"have we seen this before?" becomes automatic instead of something the agent must
remember to look up).

Offline + deterministic: no embeddings model, no network. The corpus is JSON;
point CORPUS_PATH (or the `corpus_path` arg) at your own prior RCAs/runbooks.
"""

from __future__ import annotations

import json
import math
import os
import re
from collections import Counter
from pathlib import Path

_DEFAULT_CORPUS = Path(__file__).resolve().parent / "mcp" / "servers" / "memory" / "corpus.json"


def corpus_path(explicit: str = "") -> Path:
    return Path(explicit or os.environ.get("CORPUS_PATH") or _DEFAULT_CORPUS).resolve()


def load_corpus(explicit: str = "") -> list[dict]:
    try:
        return json.loads(corpus_path(explicit).read_text())
    except (OSError, ValueError):
        return []


def _tokenize(text: str) -> list[str]:
    return re.findall(r"[a-z0-9]+", (text or "").lower())


def _doc_text(rec: dict) -> str:
    return " ".join([
        rec.get("title", ""), rec.get("summary", ""), rec.get("root_cause", ""),
        rec.get("service", ""), " ".join(rec.get("tags", [])),
    ])


def bm25_rank(query: str, records: list[dict], k1: float = 1.5, b: float = 0.75) -> list[dict]:
    """Rank records against a query with BM25. Pure + deterministic. Returns records
    annotated with a `score` (best first)."""
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


def search(query: str, limit: int = 3, corpus: str = "") -> list[dict]:
    """Top prior incidents similar to `query` (zero-score/no-overlap results dropped,
    so a miss returns nothing rather than an arbitrary closest record)."""
    ranked = bm25_rank(query, load_corpus(corpus))
    return [r for r in ranked if r["score"] > 0][:limit]


def get_record(incident_id: str, corpus: str = "") -> dict:
    for rec in load_corpus(corpus):
        if rec.get("id") == incident_id:
            return rec
    return {"error": f"no prior incident '{incident_id}' in the corpus"}
