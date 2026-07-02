"""Agent SLO metrics — recording + Prometheus exposition."""

from app.slo_metrics import SLOMetrics


def test_records_counters_and_renders_prometheus():
    m = SLOMetrics()
    m.record(latency_s=3.0, tokens=1000, success=True, abstained=False)
    m.record(latency_s=45.0, tokens=2000, success=True, abstained=True)
    m.record(latency_s=90.0, tokens=0, success=False, abstained=False)

    assert m.investigations_total == 3
    assert m.investigations_success == 2
    assert m.investigations_abstained == 1
    assert m.investigations_failed == 1
    assert m.tokens_total == 3000

    text = m.render()
    assert "copilot_investigations_total 3" in text
    assert "copilot_investigations_abstained_total 1" in text
    assert "copilot_tokens_total 3000" in text
    # Histogram is cumulative: le=5 counts the 3s run only; +Inf counts all 3.
    assert 'copilot_investigation_latency_seconds_bucket{le="5"} 1' in text
    assert 'copilot_investigation_latency_seconds_bucket{le="+Inf"} 3' in text
    assert "copilot_investigation_latency_seconds_count 3" in text


def test_render_is_valid_exposition_shape():
    m = SLOMetrics()
    m.record(1.0, 100, True, False)
    lines = m.render().strip().splitlines()
    # every non-comment line is "name value" or "name{labels} value"
    for ln in lines:
        if ln.startswith("#"):
            continue
        assert len(ln.rsplit(" ", 1)) == 2


def test_endpoint_exposed_and_open():
    import app.api.main as api
    from fastapi.testclient import TestClient

    client = TestClient(api.app)
    r = client.get("/metrics/slo")
    assert r.status_code == 200
    assert "copilot_investigations_total" in r.text
    assert "text/plain" in r.headers["content-type"]
