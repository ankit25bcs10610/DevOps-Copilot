"""Incident-storm correlation — turn many concurrent alerts into one root cause.

When several services alert at once it's usually one upstream failure cascading, not
N independent incidents. This clusters alerts that fire close together in time, then
uses the service dependency graph to rank which failing service the others most
likely cascade FROM — so the responder chases one root cause, not a storm.

Pure + deterministic (graph + time math, no LLM), so it's free and unit-testable.
The service graph is caller→callee edges (a caller depends on its callees), matching
app/mcp/servers/traces/server.py.
"""

from __future__ import annotations

from collections import deque
from datetime import datetime


def _to_epoch(ts) -> float | None:
    """Best-effort timestamp → epoch seconds (accepts a number or ISO-8601 string)."""
    if isinstance(ts, (int, float)):
        return float(ts)
    if isinstance(ts, str) and ts.strip():
        try:
            return float(ts)
        except ValueError:
            pass
        try:
            return datetime.fromisoformat(ts.replace("Z", "+00:00")).timestamp()
        except ValueError:
            return None
    return None


def cluster_by_time(events: list[dict], window_s: float = 300.0, ts_key: str = "ts") -> list[list[dict]]:
    """Group events into storms: sorted by time, a gap larger than `window_s` starts
    a new cluster. Events with an unparseable/absent timestamp each stand alone."""
    dated: list[tuple[float, dict]] = []
    undated: list[dict] = []
    for e in events:
        epoch = _to_epoch(e.get(ts_key))
        (dated.append((epoch, e)) if epoch is not None else undated.append(e))
    dated.sort(key=lambda pe: pe[0])

    clusters: list[list[dict]] = []
    last_epoch: float | None = None
    for epoch, ev in dated:
        if clusters and last_epoch is not None and epoch - last_epoch <= window_s:
            clusters[-1].append(ev)
        else:
            clusters.append([ev])
        last_epoch = epoch
    clusters.extend([e] for e in undated)  # undated events don't cluster
    return clusters


def _downstream_set(edges: list[list[str]], start: str) -> set[str]:
    """All services `start` transitively depends on (its downstream), via caller→callee."""
    adj: dict[str, list[str]] = {}
    for caller, callee in edges:
        adj.setdefault(caller, []).append(callee)
    seen: set[str] = set()
    q = deque(adj.get(start, []))
    while q:
        node = q.popleft()
        if node in seen:
            continue
        seen.add(node)
        q.extend(adj.get(node, []))
    return seen


def rank_root_causes(failing_services: list[str], edges: list[list[str]]) -> list[dict]:
    """Rank the failing services by how many OF THE OTHERS transitively depend on
    them — the more downstream a failure is, the more of the storm it explains.

    Returns [{service, explains, depends_on_failing, is_root_candidate}] best-first.
    `explains` = other failing services that (transitively) call this one; the top
    service that itself depends on no other failing service is the root candidate."""
    failing = list(dict.fromkeys(s for s in failing_services if s))  # dedupe, keep order
    fset = set(failing)
    downstream = {s: _downstream_set(edges, s) for s in failing}

    ranked: list[dict] = []
    for svc in failing:
        explains = sorted(other for other in failing if other != svc and svc in downstream[other])
        depends_on_failing = sorted(downstream[svc] & fset)
        ranked.append({
            "service": svc,
            "explains": explains,
            "explains_count": len(explains),
            "depends_on_failing": depends_on_failing,
        })
    # Most-explanatory first; tie-break toward the most-downstream (depends on fewest
    # other failing services), then name for determinism.
    ranked.sort(key=lambda r: (-r["explains_count"], len(r["depends_on_failing"]), r["service"]))
    for r in ranked:
        r["is_root_candidate"] = False
    if ranked and (ranked[0]["explains_count"] > 0 or len(ranked) == 1):
        ranked[0]["is_root_candidate"] = True
    return ranked


def correlate_storm(failing_services: list[str], edges: list[list[str]]) -> dict:
    """Full correlation over a set of concurrently-failing services: the ranked
    root-cause candidates and the single most-likely shared root (or None)."""
    ranked = rank_root_causes(failing_services, edges)
    root = next((r["service"] for r in ranked if r["is_root_candidate"]), None)
    return {
        "failing_services": list(dict.fromkeys(s for s in failing_services if s)),
        "likely_root": root,
        "ranked": ranked,
    }
