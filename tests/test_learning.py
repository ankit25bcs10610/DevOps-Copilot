"""Continual-learning incident memory — turning a resolved RCA into a reusable
runbook record, and merging learned incidents into search."""

from pathlib import Path

from app import incident_memory as mem

_GOOD_REPORT = {
    "summary": "checkout-svc 5xx from a null deref in applyDiscount.",
    "root_cause": "applyDiscount read coupon.total without guarding a missing coupon",
    "affected_services": ["checkout-svc"],
    "recommended_actions": ["Guard the null coupon", "Add a regression test"],
    "verification": {"verdict": "verified", "rationale": "sandbox proved fail->pass"},
    "abstained": False,
}


def test_build_record_maps_rca_to_corpus_schema():
    rec = mem.build_record("why 500s?", _GOOD_REPORT, date="2026-07-02", seq=3)
    assert rec["id"].startswith("LEARNED-2026-07-02-003")
    assert rec["service"] == "checkout-svc"
    assert rec["root_cause"].startswith("applyDiscount")
    assert rec["runbook"] == ["Guard the null coupon", "Add a regression test"]
    assert "checkout-svc" in rec["tags"]
    assert "applydiscount" in rec["tags"]  # salient token from the root cause
    assert rec["learned"] is True


def _point_learned_corpus(monkeypatch, tmp_path: Path) -> Path:
    target = tmp_path / "learned.json"
    monkeypatch.setattr(mem, "learned_corpus_path", lambda: target)
    return target


def test_learn_from_report_appends_and_is_searchable(monkeypatch, tmp_path):
    target = _point_learned_corpus(monkeypatch, tmp_path)
    rec = mem.learn_from_report("why 500s?", _GOOD_REPORT, date="2026-07-02")
    assert rec is not None
    assert target.exists()
    # A learned incident is merged into search (bundled + learned).
    hits = mem.search("applyDiscount coupon null deref checkout", limit=5)
    assert any(h.get("id") == rec["id"] for h in hits)


def test_learn_skips_abstained_and_rootless_reports(monkeypatch, tmp_path):
    _point_learned_corpus(monkeypatch, tmp_path)
    assert mem.learn_from_report("q", {**_GOOD_REPORT, "abstained": True}, date="2026-07-02") is None
    assert mem.learn_from_report("q", {"root_cause": ""}, date="2026-07-02") is None


def test_learn_dedupes_same_root_cause(monkeypatch, tmp_path):
    _point_learned_corpus(monkeypatch, tmp_path)
    first = mem.learn_from_report("q1", _GOOD_REPORT, date="2026-07-02")
    second = mem.learn_from_report("q2", _GOOD_REPORT, date="2026-07-03")
    assert first is not None
    assert second is None  # same root cause already learned


def test_learn_respects_feature_flag(monkeypatch, tmp_path):
    _point_learned_corpus(monkeypatch, tmp_path)
    from app.config import get_settings

    monkeypatch.setattr(get_settings(), "copilot_learn_incidents", False)
    assert mem.learn_from_report("q", _GOOD_REPORT, date="2026-07-02") is None
