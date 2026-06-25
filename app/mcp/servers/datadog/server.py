"""Datadog observability MCP server.

A real MCP server (FastMCP / stdio) that gives the agent production observability
tools. When `DD_API_KEY` and `DD_APP_KEY` are set it queries the **real Datadog
API**; otherwise it runs in OFFLINE DEMO mode against the bundled fixtures
(`LOGS_DATA_PATH`), so the whole agent stays runnable with no external account —
the same live/offline pattern as the github server.

It is a drop-in replacement for the demo `logs-metrics` server: the four tool
names and return shapes are identical (`search_logs`, `get_error_summary`,
`get_metric`, `list_services`), so the agent prompt, evals, and offline demo are
unchanged — only the live data source becomes Datadog.

Live API (per https://docs.datadoghq.com/api/latest/):
  - logs:    POST https://api.{site}/api/v2/logs/events/search
  - metrics: GET  https://api.{site}/api/v1/query
  - auth:    DD-API-KEY + DD-APPLICATION-KEY headers
Verify the exact metric queries and key scopes against your own Datadog org.

Run standalone for debugging:
    DD_API_KEY=... DD_APP_KEY=... python -m app.mcp.servers.datadog.server
"""

from __future__ import annotations

import json
import os
import time
from collections import Counter
from pathlib import Path

from mcp.server.fastmcp import FastMCP

DD_API_KEY = os.environ.get("DD_API_KEY", "").strip()
DD_APP_KEY = os.environ.get("DD_APP_KEY", "").strip()
DD_SITE = os.environ.get("DD_SITE", "datadoghq.com").strip() or "datadoghq.com"
OFFLINE = not (DD_API_KEY and DD_APP_KEY)

# Offline fixtures live here (the same data the bundled demo ships with).
DATA_DIR = Path(
    os.environ.get("LOGS_DATA_PATH", str(Path(__file__).resolve().parents[1] / "logs_metrics" / "sample_data"))
).resolve()

# Map the demo's friendly metric names to Datadog metric queries. A value that
# already looks like a Datadog query (has `{` or `:`) is passed through as-is, so
# callers can also send a raw query. Tune these to your own metrics.
_METRIC_QUERIES = {
    "error_rate_5xx": "sum:trace.http.request.errors{{service:{service}}}.as_rate()",
    "p95_latency_ms": "p95:trace.http.request.duration{{service:{service}}}",
    "requests_per_min": "sum:trace.http.request.hits{{service:{service}}}.as_rate()",
}

mcp = FastMCP("datadog")


def _as_int(value, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _base_url() -> str:
    return f"https://api.{DD_SITE}"


def _headers() -> dict:
    return {
        "DD-API-KEY": DD_API_KEY,
        "DD-APPLICATION-KEY": DD_APP_KEY,
        "Content-Type": "application/json",
    }


# --------------------------------------------------------------------------- #
# Offline fixtures (mirror the demo logs-metrics behaviour exactly)
# --------------------------------------------------------------------------- #
def _read_log_lines() -> list[str]:
    log_file = DATA_DIR / "app.log"
    if not log_file.exists():
        return []
    return [ln for ln in log_file.read_text(errors="replace").splitlines() if ln.strip()]


def _parse_line(line: str) -> tuple[str, str]:
    parts = line.split()
    return (parts[1], parts[2]) if len(parts) >= 3 else ("", "")


def _offline_search_logs(service, level, contains, limit):
    out: list[str] = []
    for line in _read_log_lines():
        lvl, svc = _parse_line(line)
        if service and svc != service:
            continue
        if level and lvl != level.upper():
            continue
        if contains and contains.lower() not in line.lower():
            continue
        out.append(line)
        if len(out) >= limit:
            break
    return out


def _offline_error_summary(service):
    counter: Counter[str] = Counter()
    for line in _read_log_lines():
        lvl, svc = _parse_line(line)
        if lvl != "ERROR" or (service and svc != service):
            continue
        msg = line.split('error="', 1)[1].split('"', 1)[0] if 'error="' in line else line
        counter[msg] += 1
    return {
        "service": service or "all",
        "total_errors": sum(counter.values()),
        "distinct_errors": len(counter),
        "breakdown": [{"message": m, "count": c} for m, c in counter.most_common()],
    }


def _offline_metric(service, metric):
    metrics_file = DATA_DIR / "metrics.json"
    if not metrics_file.exists():
        return {"error": "no metrics data available"}
    data = json.loads(metrics_file.read_text())
    svc = data.get(service)
    if svc is None:
        return {"error": f"unknown service '{service}'", "available": list(data)}
    series = svc.get(metric)
    if not series:
        return {"error": f"unknown/empty metric '{metric}'", "available": list(svc)}
    first, last = series[0].get("value"), series[-1].get("value")
    trend = "rising" if last > first else "falling" if last < first else "flat"
    return {"service": service, "metric": metric, "series": series, "trend": trend, "latest": last}


def _offline_services():
    services: set[str] = set()
    for line in _read_log_lines():
        parts = line.split()
        if len(parts) >= 3:
            services.add(parts[2])
    return sorted(services)


def _trend(values: list[float]) -> str:
    if len(values) < 2:
        return "unknown"
    return "rising" if values[-1] > values[0] else "falling" if values[-1] < values[0] else "flat"


# --------------------------------------------------------------------------- #
# Tools
# --------------------------------------------------------------------------- #
@mcp.tool()
def search_logs(
    service: str | None = None,
    level: str | None = None,
    contains: str | None = None,
    limit: int | str = 50,
) -> list[str]:
    """Search application logs (most recent first).

    Args:
        service: only logs for this service (e.g. "checkout-svc").
        level: only this level — INFO / WARN / ERROR.
        contains: only logs containing this substring.
        limit: max lines to return.
    Returns a list of "<ts> <LEVEL> <service> <message>" lines.
    """
    limit = _as_int(limit, 50)
    if OFFLINE:
        return _offline_search_logs(service, level, contains, limit)

    import httpx

    terms = []
    if service:
        terms.append(f"service:{service}")
    if level:
        status = {"WARN": "warn", "ERROR": "error", "INFO": "info"}.get(level.upper(), level.lower())
        terms.append(f"status:{status}")
    if contains:
        terms.append(contains)
    query = " ".join(terms) or "*"
    resp = httpx.post(
        f"{_base_url()}/api/v2/logs/events/search",
        headers=_headers(),
        json={"filter": {"query": query, "from": "now-1h", "to": "now"},
              "page": {"limit": min(limit, 1000)}, "sort": "-timestamp"},
        timeout=20,
    )
    resp.raise_for_status()
    lines = []
    for ev in resp.json().get("data", []):
        a = ev.get("attributes", {})
        lines.append(
            f"{a.get('timestamp', '')} {str(a.get('status', '')).upper()} "
            f"{a.get('service', '')} {a.get('message', '')}".strip()
        )
    return lines[:limit]


@mcp.tool()
def get_error_summary(service: str | None = None) -> dict:
    """Summarize ERROR-level logs grouped by message, ranked by count — the
    fastest way to spot the dominant failure."""
    if OFFLINE:
        return _offline_error_summary(service)

    import httpx

    query = "status:error" + (f" service:{service}" if service else "")
    resp = httpx.post(
        f"{_base_url()}/api/v2/logs/events/search",
        headers=_headers(),
        json={"filter": {"query": query, "from": "now-1h", "to": "now"},
              "page": {"limit": 1000}, "sort": "-timestamp"},
        timeout=20,
    )
    resp.raise_for_status()
    counter: Counter[str] = Counter()
    for ev in resp.json().get("data", []):
        msg = (ev.get("attributes", {}).get("message", "") or "").strip()[:200]
        if msg:
            counter[msg] += 1
    return {
        "service": service or "all",
        "total_errors": sum(counter.values()),
        "distinct_errors": len(counter),
        "breakdown": [{"message": m, "count": c} for m, c in counter.most_common()],
    }


@mcp.tool()
def get_metric(service: str, metric: str) -> dict:
    """Read a time-series metric for a service.

    `metric` may be a friendly name (error_rate_5xx, p95_latency_ms,
    requests_per_min) or a raw Datadog query. Returns the series plus a trend
    hint (first vs last) and the latest value.
    """
    if OFFLINE:
        return _offline_metric(service, metric)

    import httpx

    # Build the Datadog query: pass through a raw query, else map a friendly name.
    if "{" in metric or ":" in metric:
        query = metric
    else:
        tmpl = _METRIC_QUERIES.get(metric)
        if not tmpl:
            return {"error": f"unknown metric '{metric}'", "available": list(_METRIC_QUERIES)}
        query = tmpl.format(service=service)

    now = int(time.time())
    resp = httpx.get(
        f"{_base_url()}/api/v1/query",
        headers=_headers(),
        params={"from": now - 3600, "to": now, "query": query},
        timeout=20,
    )
    resp.raise_for_status()
    series = resp.json().get("series") or []
    if not series:
        return {"service": service, "metric": metric, "query": query, "series": [],
                "trend": "unknown", "latest": None}
    points = [{"ts": int(ts / 1000), "value": val} for ts, val in series[0].get("pointlist", [])]
    values = [p["value"] for p in points if p["value"] is not None]
    return {
        "service": service,
        "metric": metric,
        "query": query,
        "series": points,
        "trend": _trend(values),
        "latest": values[-1] if values else None,
    }


@mcp.tool()
def list_services() -> list[str]:
    """List the distinct services currently emitting logs."""
    if OFFLINE:
        return _offline_services()

    import httpx

    resp = httpx.post(
        f"{_base_url()}/api/v2/logs/events/search",
        headers=_headers(),
        json={"filter": {"query": "*", "from": "now-1h", "to": "now"}, "page": {"limit": 1000}},
        timeout=20,
    )
    resp.raise_for_status()
    services = {
        ev.get("attributes", {}).get("service")
        for ev in resp.json().get("data", [])
        if ev.get("attributes", {}).get("service")
    }
    return sorted(services)


if __name__ == "__main__":
    mcp.run(transport="stdio")
