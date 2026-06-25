"""File-backed metrics/logs source for the UI dashboard.

Reads the same data the logs-metrics MCP server exposes (from
`runtime.logs_path()`), so the console/landing charts can show the REAL demo
signal instead of hardcoded arrays.

This is the "file" connector. To go live, swap `read_all`/`error_summary` for a
Prometheus/Loki/Datadog client — mirroring the GitHub live-vs-offline pattern —
without changing the `/metrics` API contract the frontend consumes.
"""

from __future__ import annotations

import json
from collections import Counter

from app import runtime


def _metrics_file():
    return runtime.logs_path() / "metrics.json"


def _log_file():
    return runtime.logs_path() / "app.log"


def error_summary() -> dict:
    """Group ERROR log lines by their error= message (most common first)."""
    lf = _log_file()
    if not lf.exists():
        return {"total_errors": 0, "breakdown": []}
    counter: Counter[str] = Counter()
    for line in lf.read_text(errors="replace").splitlines():
        parts = line.split()
        if len(parts) < 3 or parts[1] != "ERROR":
            continue
        if 'error="' in line:
            msg = line.split('error="', 1)[1].split('"', 1)[0]
        else:
            msg = line
        counter[msg] += 1
    return {
        "total_errors": sum(counter.values()),
        "breakdown": [{"message": m, "count": c} for m, c in counter.most_common()],
    }


def read_all() -> dict:
    """Return every service's metric series plus an error summary."""
    services: dict = {}
    mf = _metrics_file()
    if mf.exists():
        try:
            services = json.loads(mf.read_text())
        except (ValueError, OSError):
            services = {}
    return {"services": services, "error_summary": error_summary()}
