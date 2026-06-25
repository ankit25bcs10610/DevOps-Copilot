"""Custom Logs & Metrics MCP server.

This is a *real* MCP server built with the official `mcp` Python SDK (FastMCP).
It speaks the Model Context Protocol over stdio, so any MCP client — our
LangGraph agent, Claude Desktop, etc. — can discover and call its tools without
knowing anything about how it works internally.

Tools exposed:
  - search_logs:        filter log lines by service / level / substring
  - get_error_summary:  group ERROR lines by message and count them
  - get_metric:         read a named time-series metric for a service
  - list_services:      list services that appear in the logs

Run standalone for debugging:
    python -m app.mcp.servers.logs_metrics.server
"""

from __future__ import annotations

import json
import os
from collections import Counter
from pathlib import Path

from mcp.server.fastmcp import FastMCP

# Data directory is configurable so the same server works locally and in Docker.
DATA_DIR = Path(
    os.environ.get(
        "LOGS_DATA_PATH",
        str(Path(__file__).parent / "sample_data"),
    )
).resolve()

mcp = FastMCP("logs-metrics")


def _as_int(value, default: int) -> int:
    """Coerce a tool argument to int. LLMs (esp. Llama) often send numbers as
    strings; we accept either rather than failing schema validation."""
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _read_log_lines() -> list[str]:
    log_file = DATA_DIR / "app.log"
    if not log_file.exists():
        return []
    # errors="replace" so a stray non-UTF-8 byte can't crash every log tool.
    return [ln for ln in log_file.read_text(errors="replace").splitlines() if ln.strip()]


@mcp.tool()
def list_services() -> list[str]:
    """List the distinct service names that appear in the logs."""
    services: set[str] = set()
    for line in _read_log_lines():
        # Format: <ts> <LEVEL> <service> ...
        parts = line.split()
        if len(parts) >= 3:
            services.add(parts[2])
    return sorted(services)


def _parse_line(line: str) -> tuple[str, str]:
    """Return (level, service) from a log line of the form `<ts> <LEVEL> <svc> …`.
    Used for exact-column matching instead of fragile substring checks."""
    parts = line.split()
    return (parts[1], parts[2]) if len(parts) >= 3 else ("", "")


@mcp.tool()
def search_logs(
    service: str | None = None,
    level: str | None = None,
    contains: str | None = None,
    limit: int | str = 50,
) -> list[str]:
    """Search application logs.

    Args:
        service: only return lines for this service (e.g. "checkout-svc").
        level: only return lines at this level (INFO/WARN/ERROR).
        contains: only return lines containing this substring.
        limit: maximum number of matching lines to return.
    """
    limit = _as_int(limit, 50)
    results: list[str] = []
    for line in _read_log_lines():
        lvl, svc = _parse_line(line)
        if service and svc != service:
            continue
        if level and lvl != level.upper():
            continue
        if contains and contains.lower() not in line.lower():
            continue
        results.append(line)
        if len(results) >= limit:
            break
    return results


@mcp.tool()
def get_error_summary(service: str | None = None) -> dict:
    """Summarize ERROR-level log lines, grouped by their error message.

    Returns a dict with the total error count and a ranked breakdown — the
    fastest way for the agent to spot the dominant failure.
    """
    counter: Counter[str] = Counter()
    for line in _read_log_lines():
        lvl, svc = _parse_line(line)
        if lvl != "ERROR":
            continue
        if service and svc != service:
            continue
        # Pull out the error="..." payload if present, else use the tail.
        # Bound to the closing quote so trailing fields after error="..." don't
        # leak into the message (which would split one error into many groups).
        if 'error="' in line:
            msg = line.split('error="', 1)[1].split('"', 1)[0]
        else:
            msg = line
        counter[msg] += 1

    ranked = [{"message": m, "count": c} for m, c in counter.most_common()]
    return {
        "service": service or "all",
        "total_errors": sum(counter.values()),
        "distinct_errors": len(counter),
        "breakdown": ranked,
    }


@mcp.tool()
def get_metric(service: str, metric: str) -> dict:
    """Read a named time-series metric for a service.

    Common metrics: "error_rate_5xx", "p95_latency_ms", "requests_per_min".
    Returns the raw series plus a tiny trend hint (first vs last value).
    """
    metrics_file = DATA_DIR / "metrics.json"
    if not metrics_file.exists():
        return {"error": "no metrics data available"}

    data = json.loads(metrics_file.read_text())
    svc = data.get(service)
    if svc is None:
        return {"error": f"unknown service '{service}'", "available": list(data)}
    series = svc.get(metric)
    if series is None:
        return {"error": f"unknown metric '{metric}'", "available": list(svc)}
    if not series:
        return {"error": f"metric '{metric}' has no data points",
                "service": service, "metric": metric}

    first, last = series[0].get("value"), series[-1].get("value")
    if first is None or last is None:
        trend = "unknown"
    elif last > first:
        trend = "rising"
    elif last < first:
        trend = "falling"
    else:
        trend = "flat"

    return {
        "service": service,
        "metric": metric,
        "series": series,
        "trend": trend,
        "latest": last,
    }


if __name__ == "__main__":
    # stdio transport — the standard way MCP clients launch a server process.
    mcp.run(transport="stdio")
