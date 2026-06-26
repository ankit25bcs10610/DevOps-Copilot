"""Offline behaviour of the real MCP connectors (datadog observability,
pagerduty alerting, kubernetes, sentry). Live API paths need real keys; these
cover the offline fixtures the demo + agent rely on."""

import pytest

from app.mcp.servers.datadog import server as dd
from app.mcp.servers.github import server as gh
from app.mcp.servers.kubernetes import server as k8s
from app.mcp.servers.pagerduty import server as pd
from app.mcp.servers.sentry import server as sentry
from app.mcp.servers.traces import server as traces


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


# --- Kubernetes (offline fixtures tie into the bad checkout-svc deploy) ---- #
def test_k8s_offline_lists_crashlooping_pod():
    if not k8s.OFFLINE:
        pytest.skip("KUBE_CONFIG_PATH is set — offline fixtures inactive")
    crash = k8s.list_pods(status="CrashLoopBackOff")
    assert crash and crash[0]["name"].startswith("checkout-svc")


def test_k8s_offline_describe_pod_shows_failure():
    if not k8s.OFFLINE:
        pytest.skip("KUBE_CONFIG_PATH is set")
    pod = k8s.list_pods(status="CrashLoopBackOff")[0]
    d = k8s.describe_pod(pod["name"])
    assert d["restarts"] >= 1
    assert any(e["type"] == "Warning" for e in d["events"])


def test_k8s_offline_deployment_rollout_is_stuck_and_ties_to_commit():
    if not k8s.OFFLINE:
        pytest.skip("KUBE_CONFIG_PATH is set")
    dep = k8s.get_deployment_status("checkout-svc")
    assert dep["unavailable"] >= 1
    hist = k8s.rollout_history("checkout-svc")
    assert any("discount" in r["change_cause"].lower() for r in hist)


def test_k8s_offline_write_tools_are_simulated():
    if not k8s.OFFLINE:
        pytest.skip("KUBE_CONFIG_PATH is set")
    res = k8s.scale_deployment("checkout-svc", 3)
    assert "simulated" in res["status"]
    assert res["replicas"] == 3
    bad = k8s.scale_deployment("checkout-svc", -1)
    assert "error" in bad


# --- Sentry (offline fixtures pinpoint the discount regression) ----------- #
def test_sentry_offline_lists_regression_issue():
    if not sentry.OFFLINE:
        pytest.skip("SENTRY_API_TOKEN is set")
    issues = sentry.list_issues()
    assert issues and "applyDiscount" in issues[0]["culprit"]
    assert issues[0]["is_regression"] is True


def test_sentry_offline_latest_event_has_stacktrace_to_checkout():
    if not sentry.OFFLINE:
        pytest.skip("SENTRY_API_TOKEN is set")
    ev = sentry.get_latest_event("SENTRY-4011")
    assert ev["stacktrace"][0]["filename"] == "checkout.js"
    assert ev["stacktrace"][0]["lineno"] == 42


# --- GitHub Actions CI (offline fixtures) --------------------------------- #
def test_github_offline_workflow_runs_show_failure():
    if not gh.OFFLINE:
        pytest.skip("GITHUB_TOKEN is set")
    runs = gh.list_workflow_runs()
    assert any(r["conclusion"] == "failure" for r in runs)


def test_github_offline_failed_job_logs_show_the_bug():
    if not gh.OFFLINE:
        pytest.skip("GITHUB_TOKEN is set")
    logs = gh.get_failed_job_logs(980001)
    assert "applyDiscount" in logs["failed_jobs"][0]["log_tail"]


# --- "what changed" correlation (pure scorer, env-independent) ------------- #
def test_correlate_changes_ranks_discount_commit_top():
    commits = [
        {"sha": "abc1234", "date": "2026-06-23", "message": "Add percentage discount support to checkout"},
        {"sha": "9f8e7d6", "date": "2026-06-22", "message": "Refactor cart total calculation"},
        {"sha": "1122334", "date": "2026-06-21", "message": "Bump dependencies"},
    ]
    ranked = gh._rank_commits("applyDiscount checkout.js discount 500", commits)
    assert ranked[0]["sha"] == "abc1234"
    assert ranked[0]["score"] >= 1
    assert "discount" in ranked[0]["matched"]


# --- PagerDuty write actions (offline simulated) -------------------------- #
def test_pagerduty_offline_writes_are_simulated():
    if not pd.OFFLINE:
        pytest.skip("PAGERDUTY_API_TOKEN is set")
    assert "simulated" in pd.add_incident_note("PINC4242", "RCA: null deref")["status"]
    assert "simulated" in pd.acknowledge_incident("PINC4242")["status"]
    assert "simulated" in pd.resolve_incident("PINC4242")["status"]


# --- Datadog anomaly detection (pure analyzer, env-independent) ------------ #
def test_detect_anomaly_flags_5xx_spike():
    res = dd._detect_anomaly([0.0, 0.02, 0.71])
    assert res["anomaly"] is True
    assert res["direction"] == "spike up"
    assert res["change_point"]["index"] == 2


def test_detect_anomaly_quiet_on_flat_series():
    res = dd._detect_anomaly([100.0, 101.0, 99.0, 100.0])
    assert res["anomaly"] is False


def test_detect_anomaly_handles_insufficient_data():
    res = dd._detect_anomaly([1.0])
    assert res["anomaly"] is False
    assert "insufficient" in res["verdict"]


# --- Traces + blast radius (pure graph fns, env-independent) --------------- #
def test_deps_from_traces_builds_service_graph():
    edges = traces.deps_from_traces(traces._DEMO_TRACES)
    assert ["api-gateway", "checkout-svc"] in edges
    assert ["checkout-svc", "inventory-svc"] in edges


def test_blast_radius_separates_cause_from_symptom():
    edges = [["api-gateway", "checkout-svc"], ["checkout-svc", "inventory-svc"],
             ["checkout-svc", "payment-svc"]]
    br = traces.blast_radius(edges, "checkout-svc")
    # api-gateway is affected (calls checkout, transitively upstream)
    assert "api-gateway" in br["affected_upstream"]
    # inventory/payment are downstream dependencies (candidate causes), not affected
    assert set(br["downstream_dependencies"]) == {"inventory-svc", "payment-svc"}


def test_traces_offline_error_trace_localizes_to_applydiscount():
    if not traces.OFFLINE:
        pytest.skip("TRACES_API_URL is set")
    errs = traces.search_traces(error_only=True)
    assert errs and errs[0]["status"] == "error"
    tr = traces.get_trace(errs[0]["trace_id"])
    bad = [sp for sp in tr["spans"] if sp.get("status") == "error" and "error" in sp]
    assert bad and "applyDiscount" in bad[0]["error"]


def test_analyze_blast_radius_offline_integration():
    if not traces.OFFLINE:
        pytest.skip("TRACES_API_URL is set")
    res = traces.analyze_blast_radius("checkout-svc")
    assert "api-gateway" in res["affected_upstream"]


# --- Next-wave analysis tools (pure functions) ---------------------------- #
def test_analyze_spans_attributes_self_time_and_finds_fault():
    spans = traces._DEMO_TRACES[0]["spans"]
    res = traces.analyze_spans(spans)
    # checkout-svc 'checkout' span has the highest self-time (its children are fast)
    assert res["bottlenecks"][0]["service"] == "checkout-svc"
    # the FAULT is the deepest error span — applyDiscount — not the slow parent
    assert res["fault_span"]["operation"] == "applyDiscount"
    assert "applyDiscount" in res["fault_span"]["error"]


def test_get_exemplars_window_join():
    if not traces.OFFLINE:
        pytest.skip("TRACES_API_URL is set")
    hits = traces.get_exemplars("2026-06-23T10:00:00Z", "2026-06-23T10:10:00Z", error_only=True)
    assert hits and hits[0]["status"] == "error"


def test_burn_rate_pages_on_fast_burn():
    res = dd._burn_rate([0.0, 0.02, 0.71], slo=0.999)
    assert res["burn_rate_short"] > 14.4
    assert "page" in res["verdict"]


def test_burn_rate_ok_within_budget():
    res = dd._burn_rate([0.0, 0.0, 0.0002], slo=0.999)
    assert "ok" in res["verdict"]


def test_onset_timeline_orders_changepoints():
    if not dd.OFFLINE:
        pytest.skip("DD keys set")
    events = dd.onset_timeline()
    assert any(e.get("service") == "checkout-svc" for e in events)


def test_first_bad_deploy_picks_last_change_before_onset():
    commits = [
        {"sha": "abc1234", "date": "2026-06-23", "message": "Add discount"},
        {"sha": "9f8e7d6", "date": "2026-06-22", "message": "Refactor"},
        {"sha": "1122334", "date": "2026-06-21", "message": "Bump deps"},
    ]
    res = gh._first_bad_deploy(commits, "2026-06-23T10:01:15Z")
    assert res["suspect"]["sha"] == "abc1234"
    assert res["landed_after_onset"] == []
