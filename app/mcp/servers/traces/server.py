"""Distributed-traces MCP server — span search + blast-radius reasoning.

Traces are how a copilot localizes a failure to a specific service/span and
follows a request across microservices — the capability above logs+metrics, and
the input to blast-radius reasoning (separating cause from symptom). When
`TRACES_API_URL` is set it queries a Jaeger-compatible query API; otherwise it
runs in OFFLINE DEMO mode over bundled OpenTelemetry-style fixtures tied to the
checkout-svc incident (the failing span is applyDiscount in checkout-svc, while
its downstream calls are healthy — so cause ≠ symptom).

Tools:
  - search_traces:          recent traces (filter by service / errors / latency)
  - get_trace:              the full span tree for a trace
  - service_dependencies:   the service dependency graph derived from spans
  - analyze_blast_radius:   given a failing service, who is affected (upstream)
                            vs. what it depends on (downstream candidate causes)

Run standalone:
    python -m app.mcp.servers.traces.server
"""

from __future__ import annotations

import os
from collections import deque
from typing import Any

from mcp.server.fastmcp import FastMCP

TRACES_API_URL = os.environ.get("TRACES_API_URL", "").strip()
OFFLINE = not TRACES_API_URL

mcp = FastMCP("traces")


def _as_int(value, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


# --------------------------------------------------------------------------- #
# Offline fixtures — the checkout request flow, with one erroring trace.
# --------------------------------------------------------------------------- #
_DEMO_TRACES: list[dict[str, Any]] = [
    {
        "trace_id": "tr-checkout-err-001", "root_service": "api-gateway",
        "operation": "POST /api/checkout", "duration_ms": 512, "status": "error",
        "start": "2026-06-23T10:01:15Z", "span_count": 4,
        "spans": [
            {"span_id": "s1", "service": "api-gateway", "operation": "POST /api/checkout",
             "duration_ms": 512, "status": "error", "parent": None},
            {"span_id": "s2", "service": "checkout-svc", "operation": "checkout",
             "duration_ms": 480, "status": "error", "parent": "s1"},
            {"span_id": "s3", "service": "checkout-svc", "operation": "applyDiscount",
             "duration_ms": 2, "status": "error", "parent": "s2",
             "error": "TypeError: Cannot read properties of undefined (reading 'total') "
                      "at applyDiscount (checkout.js:42)"},
            {"span_id": "s4", "service": "inventory-svc", "operation": "GET /stock",
             "duration_ms": 44, "status": "ok", "parent": "s2"},
        ],
    },
    {
        "trace_id": "tr-checkout-ok-002", "root_service": "api-gateway",
        "operation": "POST /api/checkout", "duration_ms": 233, "status": "ok",
        "start": "2026-06-23T09:59:11Z", "span_count": 4,
        "spans": [
            {"span_id": "a1", "service": "api-gateway", "operation": "POST /api/checkout",
             "duration_ms": 233, "status": "ok", "parent": None},
            {"span_id": "a2", "service": "checkout-svc", "operation": "checkout",
             "duration_ms": 201, "status": "ok", "parent": "a1"},
            {"span_id": "a3", "service": "inventory-svc", "operation": "GET /stock",
             "duration_ms": 41, "status": "ok", "parent": "a2"},
            {"span_id": "a4", "service": "payment-svc", "operation": "POST /charge",
             "duration_ms": 120, "status": "ok", "parent": "a2"},
        ],
    },
]


def deps_from_traces(traces: list[dict]) -> list[list[str]]:
    """Derive caller→callee service edges from span parent links. Pure + sorted
    so the dependency graph is deterministic."""
    span_service: dict[str, str] = {}
    for tr in traces:
        for sp in tr.get("spans", []):
            span_service[sp["span_id"]] = sp["service"]
    edges: set[tuple[str, str]] = set()
    for tr in traces:
        for sp in tr.get("spans", []):
            parent = sp.get("parent")
            if parent and parent in span_service:
                caller, callee = span_service[parent], sp["service"]
                if caller != callee:
                    edges.add((caller, callee))
    return [list(e) for e in sorted(edges)]


def blast_radius(edges: list[list[str]], failing_service: str) -> dict:
    """Given service dependency edges (caller→callee) and a failing service,
    return who is AFFECTED (callers, transitively upstream) vs. its DOWNSTREAM
    dependencies (candidate causes to rule in/out). Pure + unit-testable."""
    upstream: dict[str, list[str]] = {}
    downstream: dict[str, list[str]] = {}
    for caller, callee in edges:
        downstream.setdefault(caller, []).append(callee)
        upstream.setdefault(callee, []).append(caller)

    def _bfs(graph: dict[str, list[str]], start: str) -> list[str]:
        seen: set[str] = set()
        q = deque(graph.get(start, []))
        while q:
            node = q.popleft()
            if node in seen:
                continue
            seen.add(node)
            q.extend(graph.get(node, []))
        return sorted(seen)

    return {
        "failing_service": failing_service,
        "affected_upstream": _bfs(upstream, failing_service),
        "downstream_dependencies": _bfs(downstream, failing_service),
    }


def analyze_spans(spans: list[dict]) -> dict:
    """Latency attribution + fault localization over a span tree. Pure + testable.

    self_time = a span's own duration minus its direct children's durations, so the
    bottleneck is attributed to the span actually doing the work — not its slow
    parent. Separately surfaces error spans and the *fault span* (the deepest error
    span), since on an error incident the fault is often a fast span the latency
    view would overlook.
    """
    by_id = {sp["span_id"]: sp for sp in spans}
    children: dict[str, list[dict]] = {}
    for sp in spans:
        parent = sp.get("parent")
        if parent:
            children.setdefault(parent, []).append(sp)

    def _depth(sp: dict) -> int:
        d, cur = 0, sp
        while cur.get("parent") and cur["parent"] in by_id:
            d += 1
            cur = by_id[cur["parent"]]
        return d

    bottlenecks = []
    for sp in spans:
        kids = children.get(sp["span_id"], [])
        self_time = max(0, sp.get("duration_ms", 0) - sum(c.get("duration_ms", 0) for c in kids))
        bottlenecks.append({
            "span_id": sp["span_id"], "service": sp["service"], "operation": sp["operation"],
            "duration_ms": sp.get("duration_ms", 0), "self_time_ms": self_time,
            "status": sp.get("status", "ok"),
        })
    bottlenecks.sort(key=lambda b: b["self_time_ms"], reverse=True)

    error_spans = [sp for sp in spans if sp.get("status") == "error" or sp.get("error")]
    fault = max(error_spans, key=_depth) if error_spans else None
    return {
        "bottlenecks": bottlenecks,
        "error_spans": [
            {"span_id": sp["span_id"], "service": sp["service"], "operation": sp["operation"],
             "error": sp.get("error", "")} for sp in error_spans
        ],
        "fault_span": (
            {"span_id": fault["span_id"], "service": fault["service"],
             "operation": fault["operation"], "error": fault.get("error", "")} if fault else None
        ),
    }


@mcp.tool()
def analyze_critical_path(trace_id: str) -> dict:
    """Attribute latency by SELF-time (a span's own work, not its children's) and
    localize the FAULT to the deepest error span — so the agent blames the span
    actually responsible, not its slow parent. Returns ranked bottlenecks, error
    spans, and the fault span."""
    tr = get_trace(trace_id)
    if tr.get("error"):
        return tr
    return {"trace_id": trace_id, **analyze_spans(list(tr.get("spans", [])))}


@mcp.tool()
def get_exemplars(start: str, end: str, error_only: bool = True, limit: int | str = 5) -> list[dict]:
    """Exemplar pivot: given a time window (e.g. the bucket where a metric anomaly
    fired), return representative traces whose start falls inside it — a
    deterministic anomaly → trace jump instead of guessing which trace to read.
    Timestamps are ISO-8601 UTC (lexicographic compare is chronological)."""
    limit = _as_int(limit, 5)
    rows = search_traces(error_only=error_only, limit=100)
    hits = [r for r in rows if start <= str(r.get("start", "")) <= end]
    return hits[:limit]


@mcp.tool()
def search_traces(
    service: str | None = None, error_only: bool = False,
    min_duration_ms: int | str = 0, limit: int | str = 20,
) -> list[dict]:
    """Search recent traces (summary rows). Filter by service, errors only, or a
    minimum root duration — the entry point for following a slow/failing request."""
    limit = _as_int(limit, 20)
    min_dur = _as_int(min_duration_ms, 0)
    if OFFLINE:
        out = []
        for tr in _DEMO_TRACES:
            if error_only and tr["status"] != "error":
                continue
            if min_dur and tr["duration_ms"] < min_dur:
                continue
            if service and not any(sp["service"] == service for sp in tr["spans"]):
                continue
            out.append({k: tr[k] for k in
                        ("trace_id", "root_service", "operation", "duration_ms", "status", "start", "span_count")})
        return out[:limit]

    import httpx

    params: dict = {"limit": limit}
    if service:
        params["service"] = service
    resp = httpx.get(f"{TRACES_API_URL}/api/traces", params=params, timeout=20)
    resp.raise_for_status()
    out = []
    for tr in resp.json().get("data", []):
        spans = tr.get("spans", [])
        has_err = any(
            any(t.get("key") == "error" and t.get("value") for t in sp.get("tags", []))
            for sp in spans
        )
        if error_only and not has_err:
            continue
        root = spans[0] if spans else {}
        out.append({"trace_id": tr.get("traceID"), "root_service": "", "operation": root.get("operationName"),
                    "duration_ms": round(root.get("duration", 0) / 1000), "status": "error" if has_err else "ok",
                    "span_count": len(spans)})
    return out[:limit]


@mcp.tool()
def get_trace(trace_id: str) -> dict:
    """Return the full span tree for a trace (service, operation, duration, status,
    parent, error) — to localize exactly which span failed or was slow."""
    if OFFLINE:
        tr = next((t for t in _DEMO_TRACES if t["trace_id"] == trace_id), None)
        return tr or {"error": f"unknown trace '{trace_id}' (offline demo has "
                      f"{[t['trace_id'] for t in _DEMO_TRACES]})"}

    import httpx

    resp = httpx.get(f"{TRACES_API_URL}/api/traces/{trace_id}", timeout=20)
    resp.raise_for_status()
    data = (resp.json().get("data") or [{}])[0]
    return {"trace_id": trace_id, "spans": [
        {"span_id": sp.get("spanID"), "operation": sp.get("operationName"),
         "duration_ms": round(sp.get("duration", 0) / 1000)} for sp in data.get("spans", [])]}


@mcp.tool()
def service_dependencies() -> list[list[str]]:
    """Return the runtime service dependency graph as caller→callee edges, derived
    from observed spans (not static config)."""
    if OFFLINE:
        return deps_from_traces(_DEMO_TRACES)
    return deps_from_traces([get_trace(t["trace_id"]) for t in search_traces(limit=100)])


@mcp.tool()
def analyze_blast_radius(failing_service: str) -> dict:
    """Given a failing service, compute who is AFFECTED (upstream callers,
    transitively) vs. its DOWNSTREAM dependencies (candidate causes) — the
    cause-vs-symptom separation that focuses an investigation."""
    edges = service_dependencies()
    result = blast_radius(edges, failing_service)
    result["edges"] = edges
    return result


if __name__ == "__main__":
    mcp.run(transport="stdio")
