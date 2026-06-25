"""Offline behaviour of the real MCP connectors (datadog observability,
pagerduty alerting). Live API paths need real keys; these cover the offline
fixtures the demo + agent rely on."""

import pytest

from app.mcp.servers.datadog import server as dd
from app.mcp.servers.pagerduty import server as pd


# --- Datadog (offline helpers are env-independent) ----------------------- #
def test_datadog_offline_error_summary_finds_checkout_bug():
    s = dd._offline_error_summary("checkout-svc")
    assert s["total_errors"] >= 1
    assert "applyDiscount" in s["breakdown"][0]["message"]


def test_datadog_offline_lists_services():
    assert "checkout-svc" in dd._offline_services()


def test_datadog_offline_metric_has_trend_and_series():
    m = dd._offline_metric("checkout-svc", "error_rate_5xx")
    assert m.get("series")
    assert m.get("trend") in ("rising", "falling", "flat")


# --- PagerDuty (offline fixtures tie into the same incident) -------------- #
def test_pagerduty_offline_incident_and_alerts():
    if not pd.OFFLINE:
        pytest.skip("PAGERDUTY_API_TOKEN is set — offline fixtures inactive")
    incidents = pd.list_incidents()
    assert incidents and incidents[0]["service"] == "checkout-svc"
    alerts = pd.get_incident_alerts(pd._DEMO_INCIDENT["id"])
    assert alerts and "5xx" in alerts[0]["summary"]
