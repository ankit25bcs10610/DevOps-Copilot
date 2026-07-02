"""Action policy engine — the graduated approve/notify/allow gate."""

from app import policy


def test_reads_are_allowed_no_approval():
    cls = policy.classify("search_logs", {"service": "checkout-svc"})
    assert cls["decision"] == "allow"
    assert cls["risk"] == "low"
    assert cls["write"] is False
    assert policy.requires_approval("search_logs") is False


def test_unknown_tool_defaults_to_allow():
    # Opt-out-safe: an unlisted tool is treated as a read, never silently a write.
    assert policy.classify("some_future_read_tool")["decision"] == "allow"


def test_create_pull_request_requires_approval():
    cls = policy.classify("create_pull_request", {"title": "Fix", "head": "fix", "base": "main"})
    assert cls["decision"] == "approve"
    assert cls["write"] is True
    assert policy.requires_approval("create_pull_request") is True
    assert "Fix" in cls["preview"]  # preview renders the PR title


def test_notify_class_runs_without_approval_but_is_a_write():
    cls = policy.classify("acknowledge_incident", {"incident_id": "PINC1"})
    assert cls["decision"] == "notify"
    assert cls["write"] is True
    assert policy.requires_approval("acknowledge_incident") is False  # notify != approve


def test_scale_to_zero_escalates_to_high_risk_approve():
    normal = policy.classify("scale_deployment", {"deployment": "checkout", "replicas": 3})
    assert normal["decision"] == "approve" and normal["risk"] == "high"
    to_zero = policy.classify("scale_deployment", {"deployment": "checkout", "replicas": 0})
    assert to_zero["decision"] == "approve"
    assert to_zero["risk"] == "high"
    assert "ZERO" in to_zero["why"] or "outage" in to_zero["why"].lower()


def test_approve_tools_set_matches_policy():
    assert "create_pull_request" in policy.APPROVE_TOOLS
    assert "scale_deployment" in policy.APPROVE_TOOLS
    # notify-class tools are NOT in the approval set
    assert "acknowledge_incident" not in policy.APPROVE_TOOLS


# --- confidence gate ------------------------------------------------------- #
def test_evidence_confidence_tiers():
    assert policy.evidence_confidence(0) == "low"
    assert policy.evidence_confidence(1) == "low"
    assert policy.evidence_confidence(2) == "medium"
    assert policy.evidence_confidence(4) == "high"


def test_auto_approvable_by_risk_and_confidence():
    # High-risk needs high confidence; low-risk needs any.
    assert policy.auto_approvable("high", "high") is True
    assert policy.auto_approvable("high", "medium") is False
    assert policy.auto_approvable("medium", "medium") is True
    assert policy.auto_approvable("medium", "low") is False
    assert policy.auto_approvable("low", "low") is True


def test_confidence_gate_blocks_high_risk_on_thin_evidence():
    calls = [{"name": "scale_deployment", "args": {"replicas": 0}}]  # high risk
    gate = policy.confidence_gate(calls, evidence_count=1)  # low confidence
    assert gate["highest_risk"] == "high"
    assert gate["confidence"] == "low"
    assert gate["auto_approve_blocked"] is True
    assert gate["reason"]


def test_confidence_gate_allows_high_risk_with_strong_evidence():
    calls = [{"name": "scale_deployment", "args": {"replicas": 2}}]  # high risk
    gate = policy.confidence_gate(calls, evidence_count=5)  # high confidence
    assert gate["auto_approve_blocked"] is False


def test_confidence_gate_ignores_reads():
    gate = policy.confidence_gate([{"name": "search_logs", "args": {}}], evidence_count=0)
    assert gate["auto_approve_blocked"] is False
    assert gate["highest_risk"] == "low"
