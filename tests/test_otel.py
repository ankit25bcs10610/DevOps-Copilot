"""OpenTelemetry wiring — must be a zero-cost no-op unless configured."""

from app import otel


def test_disabled_by_default(monkeypatch):
    monkeypatch.delenv("OTEL_EXPORTER_OTLP_ENDPOINT", raising=False)
    otel._reset_for_tests()
    assert otel.init_tracing() is False
    assert otel.enabled() is False


def test_span_is_noop_when_disabled():
    otel._reset_for_tests()
    # Must not raise, and yields None (no active span) when tracing is off.
    with otel.span("investigation.turn", thread_id="t1") as sp:
        assert sp is None


def test_span_noop_swallows_attributes():
    otel._reset_for_tests()
    # Passing attributes on a disabled span is harmless.
    with otel.span("mcp.tools", count=3, service="checkout-svc"):
        pass
