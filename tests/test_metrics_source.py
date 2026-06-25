"""Metrics/logs file source — also covers the error= parse on real sample data."""

from app import metrics_source, runtime


def test_read_all_exposes_checkout_metric():
    runtime.reset()
    data = metrics_source.read_all()
    assert "checkout-svc" in data["services"]
    assert "error_rate_5xx" in data["services"]["checkout-svc"]


def test_error_summary_groups_checkout_typeerror():
    runtime.reset()
    summary = metrics_source.error_summary()
    assert summary["total_errors"] >= 1
    # All the ERROR lines share one applyDiscount TypeError → one dominant bucket.
    top = summary["breakdown"][0]
    assert "applyDiscount" in top["message"]
    assert top["count"] >= 1
