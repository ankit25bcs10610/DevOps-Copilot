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
_PROJECT_ROOT = Path(__file__).resolve().parent.parent


def corpus_path(explicit: str = "") -> Path:
    return Path(explicit or os.environ.get("CORPUS_PATH") or _DEFAULT_CORPUS).resolve()


def learned_corpus_path() -> Path:
    """Where continually-learned incidents accumulate — separate from the bundled
    demo corpus so the shipped fixture is never mutated."""
    from app.config import get_settings

    base = get_settings().copilot_learned_corpus.strip()
    return Path(base).expanduser().resolve() if base else (_PROJECT_ROOT / "learned_incidents.json")


def _read_json(path: Path) -> list[dict]:
    try:
        data = json.loads(path.read_text())
        return data if isinstance(data, list) else []
    except (OSError, ValueError):
        return []


def load_corpus(explicit: str = "") -> list[dict]:
    """Load the incident corpus. With no explicit/env path, merges the bundled
    corpus with continually-learned incidents (deduped by id); an explicit path is
    returned verbatim so callers/tests can pin an isolated corpus."""
    base = _read_json(corpus_path(explicit))
    if explicit or os.environ.get("CORPUS_PATH"):
        return base
    seen = {r.get("id") for r in base}
    learned = [r for r in _read_json(learned_corpus_path()) if r.get("id") not in seen]
    return base + learned


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


# --------------------------------------------------------------------------- #
# Continual learning — turn a resolved investigation into a reusable runbook.
# After a confident RCA, we append a corpus record so the next similar incident
# warm-starts from it. Institutional memory that compounds instead of a static
# fixture. Pure record-building (testable); the write is best-effort.
# --------------------------------------------------------------------------- #
_TAG_STOP = {
    "the", "and", "for", "with", "that", "this", "from", "into", "was", "were",
    "has", "have", "not", "but", "its", "are", "out", "due", "via", "when", "read",
    "properties", "property", "cannot", "reading", "error", "errors", "service",
}


def _tags_from(report: dict, limit: int = 8) -> list[str]:
    """Salient keywords for BM25 recall: affected services + distinctive tokens
    from the root cause. Deterministic and order-stable."""
    tags: list[str] = []
    for s in report.get("affected_services") or []:
        t = str(s).strip().lower()
        if t and t not in tags:
            tags.append(t)
    for tok in _tokenize(report.get("root_cause") or ""):
        if len(tok) >= 4 and tok not in _TAG_STOP and tok not in tags:
            tags.append(tok)
        if len(tags) >= limit:
            break
    return tags[:limit]


def build_record(request: str, report: dict, date: str, seq: int = 0) -> dict:
    """Map a finished RCA report to a corpus record (same schema as the bundled
    corpus). Pure — `date`/`seq` are passed in so it's fully deterministic."""
    services = report.get("affected_services") or []
    verification = report.get("verification") or {}
    resolution = ""
    if verification.get("verdict") == "verified":
        resolution = "Fix verified: " + (verification.get("rationale") or "").strip()
    actions = report.get("recommended_actions") or []
    title = (report.get("summary") or request or "Investigated incident").strip()
    slug = "-".join(_tokenize(title)[:5]) or "incident"
    return {
        "id": f"LEARNED-{date}-{seq:03d}-{slug}"[:80],
        "title": title[:160],
        "date": date,
        "service": (services[0] if services else "").strip(),
        "tags": _tags_from(report),
        "summary": (report.get("summary") or "").strip()[:600],
        "root_cause": (report.get("root_cause") or "").strip()[:400],
        "resolution": resolution[:400],
        "runbook": [str(a).strip()[:200] for a in actions][:8],
        "learned": True,
    }


def append_incident(record: dict, path: Path | None = None) -> bool:
    """Append a record to the learned corpus. Best-effort: never raises."""
    target = path or learned_corpus_path()
    try:
        records = _read_json(target)
        records.append(record)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(records, indent=2))
        return True
    except OSError:
        return False


def learn_from_report(request: str, report: dict, date: str) -> dict | None:
    """Record a resolved investigation as a reusable runbook, if it's worth keeping.

    Gated to confident outcomes: a root cause must be established and the run must
    not have abstained — we don't want to pollute institutional memory with
    low-confidence guesses. Deduped by root cause so a repeated incident doesn't
    accumulate duplicates. Returns the stored record, or None if skipped."""
    from app.config import get_settings

    if not get_settings().copilot_learn_incidents:
        return None
    if report.get("abstained") or not (report.get("root_cause") or "").strip():
        return None

    rc = report["root_cause"].strip().lower()
    existing = _read_json(learned_corpus_path())
    if any((r.get("root_cause") or "").strip().lower() == rc for r in existing):
        return None  # already learned this failure mode

    record = build_record(request, report, date=date, seq=len(existing))
    return record if append_incident(record) else None
