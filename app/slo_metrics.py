"""Agent SLO metrics — Prometheus exposition, dependency-free.

Tracks the signals an on-call cares about for the agent itself: investigation
throughput, success/abstention rates, token cost, and latency (as a histogram +
sum/count for averages and quantile-ish alerting). Rendered in Prometheus text
exposition format at GET /metrics/slo, so it drops straight into Prometheus/Grafana
without a client library. Pure accounting → unit-testable.
"""

from __future__ import annotations

import threading

# Latency histogram buckets (seconds) — cumulative "le" buckets, Prometheus-style.
_BUCKETS = (1, 2, 5, 10, 20, 30, 60, 120)


class SLOMetrics:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.investigations_total = 0
        self.investigations_success = 0
        self.investigations_abstained = 0
        self.investigations_failed = 0
        self.tokens_total = 0
        self.latency_sum = 0.0
        self.latency_count = 0
        self.latency_buckets = {b: 0 for b in _BUCKETS}
        self.latency_inf = 0

    def record(self, latency_s: float, tokens: int, success: bool, abstained: bool) -> None:
        with self._lock:
            self.investigations_total += 1
            if success:
                self.investigations_success += 1
            else:
                self.investigations_failed += 1
            if abstained:
                self.investigations_abstained += 1
            self.tokens_total += max(0, int(tokens or 0))
            lat = max(0.0, float(latency_s or 0))
            self.latency_sum += lat
            self.latency_count += 1
            for b in _BUCKETS:
                if lat <= b:
                    self.latency_buckets[b] += 1
            self.latency_inf += 1

    def render(self) -> str:
        """Prometheus text exposition of the current counters/histogram."""
        with self._lock:
            lines = [
                "# HELP copilot_investigations_total Total investigations run.",
                "# TYPE copilot_investigations_total counter",
                f"copilot_investigations_total {self.investigations_total}",
                "# HELP copilot_investigations_success_total Investigations that produced a confident answer or correct abstention.",
                "# TYPE copilot_investigations_success_total counter",
                f"copilot_investigations_success_total {self.investigations_success}",
                "# HELP copilot_investigations_abstained_total Investigations that abstained.",
                "# TYPE copilot_investigations_abstained_total counter",
                f"copilot_investigations_abstained_total {self.investigations_abstained}",
                "# HELP copilot_investigations_failed_total Investigations that failed / errored.",
                "# TYPE copilot_investigations_failed_total counter",
                f"copilot_investigations_failed_total {self.investigations_failed}",
                "# HELP copilot_tokens_total Total LLM tokens spent across investigations.",
                "# TYPE copilot_tokens_total counter",
                f"copilot_tokens_total {self.tokens_total}",
                "# HELP copilot_investigation_latency_seconds Investigation wall-clock latency.",
                "# TYPE copilot_investigation_latency_seconds histogram",
            ]
            for b in _BUCKETS:
                lines.append(f'copilot_investigation_latency_seconds_bucket{{le="{b}"}} {self.latency_buckets[b]}')
            lines.append(f'copilot_investigation_latency_seconds_bucket{{le="+Inf"}} {self.latency_inf}')
            lines.append(f"copilot_investigation_latency_seconds_sum {round(self.latency_sum, 3)}")
            lines.append(f"copilot_investigation_latency_seconds_count {self.latency_count}")
            return "\n".join(lines) + "\n"


_METRICS = SLOMetrics()


def metrics() -> SLOMetrics:
    return _METRICS
