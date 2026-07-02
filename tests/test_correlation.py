"""Incident-storm correlation — time clustering + dependency-graph root ranking."""

from app.correlation import cluster_by_time, correlate_storm, rank_root_causes

# A→B→C: A depends on B depends on C. C is the deepest downstream (root) service.
_CHAIN = [["frontend", "checkout-svc"], ["checkout-svc", "payments-svc"]]


def test_rank_root_causes_picks_deepest_downstream():
    ranked = rank_root_causes(["frontend", "checkout-svc", "payments-svc"], _CHAIN)
    assert ranked[0]["service"] == "payments-svc"      # everyone cascades from payments
    assert ranked[0]["is_root_candidate"] is True
    assert set(ranked[0]["explains"]) == {"frontend", "checkout-svc"}
    # payments-svc depends on no other failing service -> truly downstream
    assert ranked[0]["depends_on_failing"] == []


def test_correlate_storm_reports_single_root():
    out = correlate_storm(["frontend", "checkout-svc", "payments-svc"], _CHAIN)
    assert out["likely_root"] == "payments-svc"
    assert out["failing_services"] == ["frontend", "checkout-svc", "payments-svc"]


def test_independent_failures_have_no_clear_root():
    # Two services with no dependency between them -> neither explains the other.
    out = correlate_storm(["svc-a", "svc-b"], [])
    assert out["likely_root"] is None
    assert all(r["explains_count"] == 0 for r in out["ranked"])


def test_single_failing_service_is_its_own_root():
    out = correlate_storm(["only-svc"], _CHAIN)
    assert out["likely_root"] == "only-svc"


def test_cluster_by_time_groups_within_window():
    events = [
        {"id": "1", "ts": 1000.0},
        {"id": "2", "ts": 1100.0},   # +100s -> same storm
        {"id": "3", "ts": 5000.0},   # big gap -> new storm
        {"id": "4", "ts": 5090.0},   # +90s -> with #3
    ]
    clusters = cluster_by_time(events, window_s=300)
    assert [[e["id"] for e in c] for c in clusters] == [["1", "2"], ["3", "4"]]


def test_cluster_by_time_parses_iso_and_isolates_undated():
    events = [
        {"id": "a", "ts": "2026-07-02T10:00:00Z"},
        {"id": "b", "ts": "2026-07-02T10:02:00Z"},  # +120s
        {"id": "c"},                                  # no ts -> stands alone
    ]
    clusters = cluster_by_time(events, window_s=300)
    ids = [[e["id"] for e in c] for c in clusters]
    assert ["a", "b"] in ids
    assert ["c"] in ids


def test_correlate_incidents_tool_offline_integration():
    from app.mcp.servers.traces import server as traces

    out = traces.correlate_incidents(["checkout-svc"])
    assert "likely_root" in out and "ranked" in out
    assert out["failing_services"] == ["checkout-svc"]
