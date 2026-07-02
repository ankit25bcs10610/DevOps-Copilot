"""OpenTelemetry tracing — spans per investigation turn and per MCP tool call.

Enabled by the standard OTEL_EXPORTER_OTLP_ENDPOINT env var. When it's unset (or the
OTLP exporter isn't installed) every hook degrades to a zero-cost no-op, so the
default/offline path is completely unaffected. Import-light: the SDK is only touched
inside init_tracing().
"""

from __future__ import annotations

import logging
import os
from contextlib import contextmanager
from typing import Iterator

log = logging.getLogger("devcopilot.otel")

_ENABLED = False
_tracer = None


def init_tracing(service_name: str = "devops-copilot") -> bool:
    """Wire an OTLP exporter if OTEL_EXPORTER_OTLP_ENDPOINT is set. Returns whether
    tracing is active. Safe to call once at startup; a failure degrades to no-op."""
    global _ENABLED, _tracer
    endpoint = os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT", "").strip()
    if not endpoint:
        return False
    try:
        from opentelemetry import trace
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor

        provider = TracerProvider(resource=Resource.create({"service.name": service_name}))
        provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter()))
        trace.set_tracer_provider(provider)
        _tracer = trace.get_tracer("devcopilot")
        _ENABLED = True
        log.info("OpenTelemetry tracing enabled (endpoint=%s)", endpoint)
    except Exception:  # noqa: BLE001 — missing exporter/SDK → stay a no-op
        log.warning("OpenTelemetry requested but could not initialize; tracing disabled", exc_info=True)
        _ENABLED = False
    return _ENABLED


def enabled() -> bool:
    return _ENABLED


@contextmanager
def span(name: str, **attributes: object) -> Iterator[object]:
    """Start a span (no-op when tracing is disabled). Usage: `with span("x", k=v): ...`."""
    if not _ENABLED or _tracer is None:
        yield None
        return
    with _tracer.start_as_current_span(name) as sp:
        for k, v in attributes.items():
            try:
                sp.set_attribute(k, v if isinstance(v, (str, bool, int, float)) else str(v))
            except Exception:  # noqa: BLE001 — a bad attribute must not break the span
                pass
        yield sp


def _reset_for_tests() -> None:
    global _ENABLED, _tracer
    _ENABLED, _tracer = False, None
