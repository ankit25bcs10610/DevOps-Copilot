"""Pure helpers in the graph nodes (the LLM-driven nodes need a key, so we test
the deterministic logic around them)."""

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from app.graph.nodes import (
    _coerce_str_list,
    _evidence_digest,
    _history_digest,
    _parse_report,
    _render_postmortem,
)


def test_history_digest_keeps_prior_qa_drops_tools_and_current_request():
    msgs = [
        HumanMessage(content="why 500s?"),
        AIMessage(content="", tool_calls=[{"name": "search_logs", "args": {}, "id": "1"}]),
        ToolMessage(content="a huge wall of log output", tool_call_id="1"),
        AIMessage(content="Root cause: null deref in applyDiscount."),
        HumanMessage(content="now propose the fix"),  # current request — excluded
    ]
    d = _history_digest({"messages": msgs})
    assert "why 500s?" in d  # prior user question kept
    assert "Root cause: null deref in applyDiscount." in d  # prior final answer kept
    assert "now propose the fix" not in d  # current request excluded
    assert "huge wall of log output" not in d  # tool output excluded


def test_history_digest_empty_on_first_turn():
    assert _history_digest({"messages": [HumanMessage(content="first question")]}) == ""
    assert _history_digest({"messages": []}) == ""


# --- RCA report synthesis (pure parsing / rendering) ----------------------- #

_GOOD_JSON = """\
Here is the report:
{
  "summary": "checkout-svc returns 500s due to a null deref in applyDiscount.",
  "severity": "sev2",
  "confidence": "high",
  "root_cause": "coupon is undefined and coupon.total throws in applyDiscount (checkout.js:42)",
  "affected_services": ["checkout-svc"],
  "hypotheses": [
    {"cause": "null deref in applyDiscount", "verdict": "validated", "confidence": "high",
     "evidence": ["app.log: TypeError ... applyDiscount (checkout.js:42)"]},
    {"cause": "downstream inventory-svc outage", "verdict": "invalidated", "confidence": "high",
     "evidence": ["inventory-svc logs show 200s"]}
  ],
  "evidence": ["error_rate_5xx rose 0.00 -> 0.71", "checkout.js:42 reads coupon.total"],
  "recommended_actions": ["Guard coupon before reading coupon.total", "Add a regression test"]
}
"""


def test_parse_report_extracts_and_normalizes_json():
    r = _parse_report(_GOOD_JSON, fallback_summary="fallback")
    assert r["severity"] == "SEV2"  # upper-cased + validated
    assert r["confidence"] == "high"
    assert "applyDiscount" in r["root_cause"]
    assert r["affected_services"] == ["checkout-svc"]
    assert len(r["hypotheses"]) == 2
    assert r["hypotheses"][0]["verdict"] == "validated"
    assert r["hypotheses"][1]["verdict"] == "invalidated"
    assert any("0.71" in e for e in r["evidence"])
    assert len(r["recommended_actions"]) == 2


def test_parse_report_falls_back_on_non_json():
    r = _parse_report("the model rambled with no json", fallback_summary="root cause: X")
    assert r["summary"] == "root cause: X"
    assert r["severity"] == "SEV3"  # safe default
    assert r["confidence"] == "low"
    assert r["root_cause"] is None
    assert r["hypotheses"] == []


def test_parse_report_coerces_bad_enums_to_safe_defaults():
    r = _parse_report(
        '{"summary": "x", "severity": "catastrophic", "confidence": "certain", '
        '"hypotheses": [{"cause": "c", "verdict": "maybe", "confidence": "kinda"}]}',
        fallback_summary="fb",
    )
    assert r["severity"] == "SEV3"  # unknown severity -> default
    assert r["confidence"] == "low"  # unknown confidence -> default
    assert r["hypotheses"][0]["verdict"] == "inconclusive"  # unknown verdict -> default
    assert r["hypotheses"][0]["confidence"] == "low"


def test_coerce_str_list_handles_mixed_shapes():
    assert _coerce_str_list("one") == ["one"]
    assert _coerce_str_list(["a", "b"]) == ["a", "b"]
    assert _coerce_str_list(None) == []
    assert _coerce_str_list([{"k": "v"}])  # dict items survive as JSON strings
    assert _coerce_str_list(["x"] * 50, limit=3) == ["x", "x", "x"]


def test_render_postmortem_includes_key_sections():
    r = _parse_report(_GOOD_JSON, fallback_summary="fb")
    md = _render_postmortem(r, "Why is checkout throwing 500s?")
    assert "# Incident Postmortem" in md
    assert "SEV2" in md
    assert "## Root cause" in md
    assert "applyDiscount" in md
    assert "## Recommended actions" in md
    assert "- [ ] Guard coupon" in md
    assert "Blameless by design" in md


def test_evidence_digest_collects_tool_output():
    msgs = [
        HumanMessage(content="why 500s?"),
        AIMessage(content="", tool_calls=[{"name": "search_logs", "args": {"service": "checkout-svc"}, "id": "1"}]),
        ToolMessage(content="ERROR checkout-svc TypeError applyDiscount", name="search_logs", tool_call_id="1"),
    ]
    d = _evidence_digest({"messages": msgs})
    assert "search_logs" in d
    assert "applyDiscount" in d
