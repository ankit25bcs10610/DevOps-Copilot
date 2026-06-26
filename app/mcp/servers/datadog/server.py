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


def _mean(xs: list[float]) -> float:
    return sum(xs) / len(xs) if xs else 0.0


def _stdev(xs: list[float]) -> float:
    if len(xs) < 2:
        return 0.0
    m = _mean(xs)
    return (sum((x - m) ** 2 for x in xs) / (len(xs) - 1)) ** 0.5


def _detect_anomaly(values: list[float], z_threshold: float = 3.0) -> dict:
    """Statistical anomaly + change-point detection over a metric series. Pure so
    it's unit-testable. Compares the latest point to the baseline (all prior
    points) via z-score, and locates the largest step change in the series.

    Returns: anomaly (bool), latest, baseline_mean, z_score, direction,
    change_point {index, from, to, delta}, and a short verdict.
    """
    clean = [float(v) for v in values if v is not None]
    if len(clean) < 3:
        return {"anomaly": False, "verdict": "insufficient data (need >= 3 points)",
                "points": len(clean)}
    baseline = clean[:-1]
    latest = clean[-1]
    mean = _mean(baseline)
    std = _stdev(baseline)
    if std > 0:
        z = (latest - mean) / std
        anomaly = abs(z) >= z_threshold
    else:
        # Flat baseline: any change from a perfectly steady signal is notable.
        z = 0.0 if latest == mean else float("inf")
        anomaly = latest != mean
    deltas = [clean[i] - clean[i - 1] for i in range(1, len(clean))]
    cp_rel = max(range(len(deltas)), key=lambda i: abs(deltas[i]))
    cp_idx = cp_rel + 1
    direction = "spike up" if latest > mean else "drop down" if latest < mean else "flat"
    z_out = round(z, 2) if z not in (float("inf"), float("-inf")) else "inf"
    return {
        "anomaly": anomaly,
        "latest": latest,
        "baseline_mean": round(mean, 4),
        "baseline_stdev": round(std, 4),
        "z_score": z_out,
        "direction": direction,
        "change_point": {
            "index": cp_idx,
            "from": round(clean[cp_idx - 1], 4),
            "to": round(clean[cp_idx], 4),
            "delta": round(deltas[cp_rel], 4),
        },
        "verdict": (
            f"anomalous {direction}: latest {latest} vs baseline mean {round(mean, 4)} "
            f"(z={z_out})" if anomaly else "within normal range"
        ),
    }


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


def _burn_rate(values: list[float], slo: float) -> dict:
    """Multi-window error-budget burn-rate (Google SRE Workbook). Pure + testable.

    burn_rate = observed_error_rate / error_budget, where error_budget = 1 - slo.
    Short window = latest point; long window = series mean. A burn rate of 1 spends
    the whole budget exactly over the SLO period; >1 spends it faster.
    """
    clean = [float(v) for v in values if v is not None]
    budget = max(1e-9, 1.0 - slo)
    if not clean:
        return {"slo": slo, "error_budget": round(budget, 6), "verdict": "no data"}
    short = clean[-1]
    long = sum(clean) / len(clean)
    burn_short = short / budget
    burn_long = long / budget
    # Standard multi-window thresholds: page on a fast burn confirmed over both windows.
    if burn_short > 14.4 and burn_long > 14.4:
        verdict = "page (fast burn — budget exhausts in hours)"
    elif burn_short > 6 and burn_long > 6:
        verdict = "page (moderate burn)"
    elif burn_short > 1:
        verdict = "ticket (slow burn — over budget)"
    else:
        verdict = "ok (within error budget)"
    hours_to_exhaustion = round(budget / short, 1) if short > 0 else None
    return {
        "slo": slo, "error_budget": round(budget, 6),
        "current_error_rate": round(short, 6), "mean_error_rate": round(long, 6),
        "burn_rate_short": round(burn_short, 2), "burn_rate_long": round(burn_long, 2),
        "hours_to_exhaustion": hours_to_exhaustion, "verdict": verdict,
    }


@mcp.tool()
def compute_burn_rate(service: str, slo: float | str = 0.999, metric: str = "error_rate_5xx") -> dict:
    """Convert an error-rate series into SLO error-budget burn rate — is this
    page-worthy, and how long until the budget is exhausted? Grounds severity in
    SRE math rather than a raw threshold.

    Args:
        service: the service to evaluate.
        slo: target availability (e.g. 0.999 = 99.9%).
        metric: the error-rate metric to read.
    """
    try:
        slo = float(slo)
    except (TypeError, ValueError):
        slo = 0.999
    data = get_metric(service, metric)
    if data.get("error"):
        return data
    values = [p.get("value") for p in data.get("series", []) if isinstance(p, dict)]
    result = _burn_rate([v for v in values if v is not None], slo)
    result.update({"service": service, "metric": metric})
    return result


@mcp.tool()
def onset_timeline() -> list[dict]:
    """Reconstruct the onset ordering across all services' metrics: for each series
    with a detected change-point, the timestamp it shifted, ordered earliest-first —
    'who moved first' (a chain implies a cascade; near-simultaneous implies a shared
    cause). Decisive for narrowing root cause. (Offline fixture mode.)"""
    if not OFFLINE:
        return [{"note": "onset_timeline runs over the offline metric fixtures; "
                 "use detect_anomaly per service in live mode."}]
    metrics_file = DATA_DIR / "metrics.json"
    if not metrics_file.exists():
        return [{"error": "no metrics data available"}]
    data = json.loads(metrics_file.read_text())
    events = []
    for svc, metrics in data.items():
        for metric, series in metrics.items():
            values = [p.get("value") for p in series if isinstance(p, dict)]
            det = _detect_anomaly([v for v in values if v is not None])
            if det.get("anomaly"):
                cp = det.get("change_point", {})
                idx = cp.get("index", 0)
                ts = series[idx].get("ts") if 0 <= idx < len(series) else None
                events.append({"service": svc, "metric": metric, "onset_ts": ts,
                               "from": cp.get("from"), "to": cp.get("to"),
                               "direction": det.get("direction")})
    events.sort(key=lambda e: str(e.get("onset_ts") or ""))
    return events


@mcp.tool()
def detect_anomaly(service: str, metric: str) -> dict:
    """Detect anomalies / change-points in a service metric.

    Reads the metric series (live Datadog or offline fixture) and reports whether
    the latest value is anomalous vs the baseline (z-score), the direction, and
    the largest step change in the window — so the agent can anchor onset and tell
    a real regression from noise instead of eyeballing the raw series.
    """
    data = get_metric(service, metric)
    if data.get("error"):
        return data
    values = [p.get("value") for p in data.get("series", []) if isinstance(p, dict)]
    result = _detect_anomaly([v for v in values if v is not None])
    result.update({"service": service, "metric": metric})
    return result


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
