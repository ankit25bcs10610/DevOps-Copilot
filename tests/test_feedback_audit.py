"""Feedback capture + the queryable audit trail."""

import pytest

from app import audit, feedback


def test_audit_records_are_queryable_newest_first():
    audit.clear()
    audit.record("approval.decided", thread="t1", approved=True)
    audit.record("security.prompt_injection_detected", tool="search_logs")
    events = audit.recent(limit=10)
    assert events[0]["event"] == "security.prompt_injection_detected"
    assert events[1]["event"] == "approval.decided"
    assert "ts" in events[0]


def test_audit_filter_by_prefix():
    audit.clear()
    audit.record("approval.decided", thread="t1", approved=False)
    audit.record("feedback.submitted", thread="t1", rating="up")
    only = audit.recent(event_prefix="feedback")
    assert len(only) == 1
    assert only[0]["event"] == "feedback.submitted"


def test_feedback_records_valid_rating(monkeypatch):
    monkeypatch.setattr(feedback, "_LOG_PATH", "")  # don't write a file in tests
    audit.clear()
    entry = feedback.record_feedback("web-1", "down", comment="missed the cache layer", question="why slow?")
    assert entry["rating"] == "down"
    assert entry["thread_id"] == "web-1"
    assert entry["comment"] == "missed the cache layer"
    # also emits an audit event
    assert any(e["event"] == "feedback.submitted" for e in audit.recent())


def test_feedback_rejects_bad_rating(monkeypatch):
    monkeypatch.setattr(feedback, "_LOG_PATH", "")
    with pytest.raises(ValueError):
        feedback.record_feedback("web-1", "meh")


# --- tamper-evident audit chain ------------------------------------------- #
def test_audit_chain_verifies_when_intact():
    audit.clear()
    audit.record("a")
    audit.record("b", x=1)
    audit.record("c")
    res = audit.verify_chain()
    assert res["valid"] is True
    assert res["count"] == 3


def test_audit_chain_detects_tampering():
    audit.clear()
    audit.record("a")
    audit.record("b", x=1)
    audit.record("c")
    # tamper with the middle entry in place
    list(audit._BUFFER)[1]["x"] = 999
    res = audit.verify_chain()
    assert res["valid"] is False
    assert res["broken_at"] == 1

