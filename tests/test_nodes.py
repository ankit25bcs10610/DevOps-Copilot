"""Pure helpers in the graph nodes (the LLM-driven nodes need a key, so we test
the deterministic logic around them)."""

from types import SimpleNamespace

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from app.graph.nodes import (
    _calibrate_confidence,
    _coerce_str_list,
    _evidence_digest,
    _history_digest,
    _over_token_budget,
    _parse_report,
    _prior_incidents_block,
    _render_postmortem,
    _verify_grounding,
)
from app.graph.state import _add_int


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


def test_prior_incidents_block_surfaces_the_precedent():
    block = _prior_incidents_block("checkout-svc 5xx errors TypeError applyDiscount discount")
    assert "PRIOR incidents" in block
    assert "discount" in block.lower()
    # phrased as something to verify, not a fact to assume
    assert "verify" in block.lower()


def test_prior_incidents_block_empty_on_no_match():
    assert _prior_incidents_block("xylophone unicorn zzzznomatch") == ""


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


def test_calibrate_confidence_high_on_strong_report():
    r = _parse_report(_GOOD_JSON, fallback_summary="fb")
    r = _calibrate_confidence(r)
    assert r["calibrated_confidence"] == "high"
    assert r["abstained"] is False
    assert r["needs"] == []


def test_calibrate_confidence_abstains_on_thin_report():
    r = _parse_report("the model rambled with no json", fallback_summary="something happened")
    r = _calibrate_confidence(r)
    assert r["calibrated_confidence"] == "low"
    assert r["abstained"] is True
    assert r["needs"]  # names what's missing


def test_verify_grounding_passes_when_evidence_is_in_tool_output():
    report = {
        "evidence": ["error_rate_5xx rose to 0.71", "applyDiscount checkout.js:42 TypeError"],
        "hypotheses": [], "calibrated_confidence": "high", "abstained": False,
    }
    digest = (
        "[search_logs] ERROR checkout-svc TypeError ... applyDiscount (checkout.js:42)\n"
        "[get_metric] error_rate_5xx series latest 0.71 trend rising"
    )
    out = _verify_grounding(report, digest)
    assert out["grounding"]["ratio"] >= 0.5
    assert out["abstained"] is False  # corroborated -> not downgraded


def test_verify_grounding_abstains_on_fabricated_evidence():
    report = {
        "evidence": ["redis cache eviction storm overwhelmed the cluster",
                     "kafka consumer lag exceeded one million messages"],
        "hypotheses": [], "calibrated_confidence": "high", "abstained": False,
    }
    digest = "[search_logs] ERROR checkout-svc TypeError applyDiscount checkout.js:42"
    out = _verify_grounding(report, digest)
    assert out["grounding"]["ratio"] < 0.5
    assert out["abstained"] is True  # not corroborated -> downgraded
    assert any("corroborated" in n for n in out["needs"])


def test_verify_grounding_noop_without_digest_or_evidence():
    out = _verify_grounding({"evidence": [], "hypotheses": []}, "")
    assert out["grounding"]["checked"] == 0


def test_postmortem_flags_insufficient_evidence_when_abstained():
    r = _calibrate_confidence(_parse_report("", fallback_summary="x"))
    md = _render_postmortem(r, "why?")
    assert "Insufficient evidence" in md


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


def test_add_int_reducer_treats_missing_as_zero():
    assert _add_int(None, 5) == 5
    assert _add_int(10, 7) == 17
    assert _add_int(3, None) == 3
    assert _add_int(None, None) == 0


def test_over_token_budget_kill_switch():
    off = SimpleNamespace(copilot_max_tokens_per_run=0)
    assert _over_token_budget(10_000, off) is False  # 0 = unlimited
    capped = SimpleNamespace(copilot_max_tokens_per_run=1000)
    assert _over_token_budget(999, capped) is False
    assert _over_token_budget(1000, capped) is True
    assert _over_token_budget(5000, capped) is True


def test_evidence_digest_collects_tool_output():
    msgs = [
        HumanMessage(content="why 500s?"),
        AIMessage(content="", tool_calls=[{"name": "search_logs", "args": {"service": "checkout-svc"}, "id": "1"}]),
        ToolMessage(content="ERROR checkout-svc TypeError applyDiscount", name="search_logs", tool_call_id="1"),
    ]
    d = _evidence_digest({"messages": msgs})
    assert "search_logs" in d
    assert "applyDiscount" in d


def test_report_node_uses_structured_output(monkeypatch):
    """Happy path: the schema-guaranteed (with_structured_output) result is used,
    the text-parse path is NOT hit, and the raw message's tokens are counted."""
    from app.graph import nodes
    from app.graph.nodes import _RcaSchema, make_report_node

    parsed = _RcaSchema(
        summary="Checkout 5xx from a null deref.",
        severity="SEV2",
        confidence="high",
        root_cause="null deref in applyDiscount",
        affected_services=["checkout-svc"],
        hypotheses=[],
        evidence=["NPE at checkout.js:11"],
        recommended_actions=["guard the null"],
    )

    class _Structured:
        def invoke(self, _msgs):
            return {"raw": SimpleNamespace(usage_metadata={"total_tokens": 11}),
                    "parsed": parsed, "parsing_error": None}

    class _FakeLLM:
        def with_structured_output(self, _schema, include_raw=False):
            return _Structured()

        def invoke(self, _msgs):
            raise AssertionError("text-parse path must not run on structured success")

    monkeypatch.setattr(nodes, "get_llm", lambda fast=False: _FakeLLM())
    node = make_report_node()
    out = node({"messages": [HumanMessage(content="why 500s?"),
                             AIMessage(content="root cause found")]})
    assert out["report"]["root_cause"] == "null deref in applyDiscount"
    assert out["report"]["severity"] == "SEV2"
    assert out["report"]["postmortem"]  # rendered deterministically
    assert out["tokens_used"] == 11


def test_report_node_falls_back_to_text_parse_on_structured_error(monkeypatch):
    """If structured output errors, the node falls back to the robust text-parse
    path (zero regression) and the fallback call's tokens are still counted."""
    from app.graph import nodes
    from app.graph.nodes import make_report_node

    json_text = ('{"summary":"s","severity":"SEV1","confidence":"low",'
                 '"root_cause":"bad deploy abc123","affected_services":[],'
                 '"hypotheses":[],"evidence":["deploy abc123 at 14:02"],'
                 '"recommended_actions":[]}')

    class _Structured:
        def invoke(self, _msgs):
            raise RuntimeError("provider doesn't support structured output")

    class _FakeLLM:
        def with_structured_output(self, _schema, include_raw=False):
            return _Structured()

        def invoke(self, _msgs):
            return SimpleNamespace(content=json_text, usage_metadata={"total_tokens": 5})

    monkeypatch.setattr(nodes, "get_llm", lambda fast=False: _FakeLLM())
    node = make_report_node()
    out = node({"messages": [HumanMessage(content="why 500s?"),
                             AIMessage(content="a deploy caused it")]})
    assert out["report"]["root_cause"] == "bad deploy abc123"
    assert out["report"]["severity"] == "SEV1"
    assert out["tokens_used"] == 5  # structured raised before logging; fallback counts
