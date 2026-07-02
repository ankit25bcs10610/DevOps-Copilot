"""Pure helpers in the graph nodes (the LLM-driven nodes need a key, so we test
the deterministic logic around them)."""

from types import SimpleNamespace

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from langgraph.graph import END

from app.graph.edges import route_after_verify
from app.graph.nodes import (
    _calibrate_confidence,
    _coerce_str_list,
    _evidence_digest,
    _extract_proposed_fix,
    _fix_targets_cause,
    _history_digest,
    _over_token_budget,
    _parse_report,
    _parse_verification,
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
    from app.config import get_settings
    monkeypatch.setattr(get_settings(), "copilot_adversarial_critique", False)  # isolate structured path
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
    from app.config import get_settings
    monkeypatch.setattr(get_settings(), "copilot_adversarial_critique", False)  # isolate fallback path
    node = make_report_node()
    out = node({"messages": [HumanMessage(content="why 500s?"),
                             AIMessage(content="a deploy caused it")]})
    assert out["report"]["root_cause"] == "bad deploy abc123"
    assert out["report"]["severity"] == "SEV1"
    assert out["tokens_used"] == 5  # structured raised before logging; fallback counts


# --------------------------------------------------------------------------- #
# Fix-verification node + helpers
# --------------------------------------------------------------------------- #
_CAUSE_REPORT = {
    "root_cause": "null deref in applyDiscount at checkout.js:11",
    "affected_services": ["checkout-svc"],
    "evidence": ["TypeError at checkout.js:11 in checkout-svc"],
    "hypotheses": [],
    "recommended_actions": ["Guard the null in applyDiscount"],
}


def _pr_call(**args):
    return AIMessage(content="", tool_calls=[{"name": "create_pull_request", "args": args, "id": "pr1"}])


def test_extract_proposed_fix_prefers_pr_call():
    state = {"messages": [
        HumanMessage(content="why 500s?"),
        _pr_call(title="Guard null in applyDiscount", body="add check in checkout.js"),
    ]}
    fix = _extract_proposed_fix(state, {"recommended_actions": ["roll back"]})
    assert fix["has_fix"] and fix["source"] == "pr"
    assert "applyDiscount" in fix["text"] and "checkout.js" in fix["text"]


def test_extract_proposed_fix_falls_back_to_actions_when_root_cause_known():
    state = {"messages": [HumanMessage(content="why?")]}
    fix = _extract_proposed_fix(state, {"root_cause": "bad deploy", "recommended_actions": ["roll back deploy abc123"]})
    assert fix["has_fix"] and fix["source"] == "actions"
    assert "abc123" in fix["text"]


def test_extract_proposed_fix_none_for_informational_run():
    state = {"messages": [HumanMessage(content="which services exist?")]}
    fix = _extract_proposed_fix(state, {"root_cause": None, "recommended_actions": []})
    assert not fix["has_fix"] and fix["source"] == "none"


def test_fix_targets_cause_grounded_on_overlap():
    fix_text = "title: Guard null in applyDiscount\nbody: add null check in checkout.js:11 for checkout-svc"
    g = _fix_targets_cause(fix_text, _CAUSE_REPORT)
    assert g["grounded"] is True
    assert "checkout.js:11" in g["shared"] or "applydiscount" in g["shared"]


def test_fix_targets_cause_ungrounded_on_unrelated_fix():
    g = _fix_targets_cause("title: Update README\nbody: fix a typo in the docs", _CAUSE_REPORT)
    assert g["grounded"] is False


def test_parse_verification_normalizes_valid_json():
    text = ('{"verdict":"verified","addresses_cause":true,"confidence":"high",'
            '"resolution_criteria":["checkout 5xx back to <0.1%"],'
            '"residual_risks":["may miss a second null path"],"rationale":"guards the null"}')
    v = _parse_verification(text, has_fix=True)
    assert v["verdict"] == "verified"
    assert v["addresses_cause"] is True
    assert v["confidence"] == "high"
    assert v["resolution_criteria"] == ["checkout 5xx back to <0.1%"]
    assert v["residual_risks"] == ["may miss a second null path"]


def test_parse_verification_falls_back_on_garbage():
    assert _parse_verification("not json at all", has_fix=True)["verdict"] == "inconclusive"
    assert _parse_verification("", has_fix=False)["verdict"] == "no_fix_proposed"
    # Bad enums coerced to safe defaults.
    v = _parse_verification('{"verdict":"maybe","confidence":"certain"}', has_fix=True)
    assert v["verdict"] == "inconclusive" and v["confidence"] == "low"


def test_render_postmortem_includes_verification_section():
    r = _parse_report("", fallback_summary="s")
    r["verification"] = {
        "verdict": "verified", "confidence": "high", "rationale": "guards the null",
        "resolution_criteria": ["5xx back to baseline"], "residual_risks": ["edge case X"],
    }
    md = _render_postmortem(r, "why 500s?")
    assert "## Fix verification" in md
    assert "verified" in md
    assert "5xx back to baseline" in md
    assert "edge case X" in md


def test_render_postmortem_omits_verification_when_no_fix():
    r = _parse_report("", fallback_summary="s")
    r["verification"] = {"verdict": "no_fix_proposed"}
    assert "## Fix verification" not in _render_postmortem(r, "which services?")


def test_route_after_verify():
    assert route_after_verify({"status": "investigating"}) == "agent"
    assert route_after_verify({"status": "done"}) == END
    assert route_after_verify({}) == END


def _verify_llm(monkeypatch, content):
    from app.graph import nodes

    class _FakeLLM:
        def invoke(self, _msgs):
            return SimpleNamespace(content=content, usage_metadata={"total_tokens": 7})

    monkeypatch.setattr(nodes, "get_llm", lambda fast=False: _FakeLLM())


def test_verify_node_loops_back_once_on_unverified_fix(monkeypatch):
    from app.graph.nodes import make_verify_node

    _verify_llm(monkeypatch, '{"verdict":"unverified","confidence":"low",'
                             '"resolution_criteria":["5xx back to baseline"],'
                             '"rationale":"touches the wrong service"}')
    node = make_verify_node()
    state = {
        "messages": [HumanMessage(content="why 500s?"), _pr_call(title="unrelated change to billing")],
        "report": dict(_CAUSE_REPORT),
        "verify_attempts": 0,
    }
    out = node(state)
    assert out["status"] == "investigating"      # bounced back to the agent
    assert out["verify_attempts"] == 1
    assert out["feedback"]                          # targeted revision feedback set
    assert out["report"]["verification"]["verdict"] == "unverified"


def test_verify_node_does_not_loop_once_attempts_exhausted(monkeypatch):
    from app.graph.nodes import make_verify_node

    _verify_llm(monkeypatch, '{"verdict":"unverified","rationale":"still wrong"}')
    node = make_verify_node()
    state = {
        "messages": [HumanMessage(content="why?"), _pr_call(title="still unrelated")],
        "report": dict(_CAUSE_REPORT),
        "verify_attempts": 1,  # already used the one allowed revision
    }
    out = node(state)
    assert out["status"] == "done"                # finalize, don't spin
    assert "feedback" not in out


def test_verify_node_downgrades_verified_when_fix_ungrounded(monkeypatch):
    from app.graph.nodes import make_verify_node

    # LLM optimistically says "verified", but the PR text references nothing in the
    # cause (and the report's actions are unrelated too), so grounding must downgrade it.
    _verify_llm(monkeypatch, '{"verdict":"verified","confidence":"high","rationale":"looks good"}')
    node = make_verify_node()
    ungrounded_report = dict(_CAUSE_REPORT, recommended_actions=["Update the onboarding docs"])
    state = {
        "messages": [HumanMessage(content="why?"), _pr_call(title="Update README", body="fix a typo")],
        "report": ungrounded_report,
        "verify_attempts": 0,
    }
    out = node(state)
    assert out["status"] == "done"
    assert out["report"]["verification"]["verdict"] == "inconclusive"  # downgraded
    assert out["report"]["verification"]["grounding"]["grounded"] is False


def test_extract_proposed_fix_captures_patch():
    state = {"messages": [
        HumanMessage(content="why?"),
        _pr_call(title="Guard null", patch="--- a/checkout.js\n+++ b/checkout.js\n@@\n-x\n+y\n"),
    ]}
    fix = _extract_proposed_fix(state, {"recommended_actions": []})
    assert fix["has_fix"] and fix["patch"].startswith("--- a/checkout.js")


def test_verify_node_sandbox_proof_overrides_llm(monkeypatch):
    """A sandbox 'resolved' verdict upgrades an unsure LLM verdict to verified."""
    from app.config import get_settings
    from app.graph import nodes
    from app.graph.nodes import make_verify_node

    _verify_llm(monkeypatch, '{"verdict":"inconclusive","confidence":"low","rationale":"unsure"}')
    monkeypatch.setattr(nodes, "run_counterfactual",
                        lambda *a, **k: {"verdict": "resolved", "detail": "fail→pass", "applied": True})
    monkeypatch.setattr(get_settings(), "copilot_sandbox_verify", True)
    node = make_verify_node()
    state = {
        "messages": [HumanMessage(content="why?"),
                     _pr_call(title="Guard null in applyDiscount",
                              patch="--- a/checkout.js\n+++ b/checkout.js\n")],
        "report": dict(_CAUSE_REPORT),
        "verify_attempts": 0,
    }
    out = node(state)
    assert out["status"] == "done"
    assert out["report"]["verification"]["verdict"] == "verified"
    assert out["report"]["verification"]["confidence"] == "high"
    assert out["report"]["verification"]["sandbox"]["verdict"] == "resolved"


def test_verify_node_sandbox_disproof_forces_loopback(monkeypatch):
    """A sandbox 'not_resolved' verdict overrides an optimistic LLM and bounces back."""
    from app.config import get_settings
    from app.graph import nodes
    from app.graph.nodes import make_verify_node

    _verify_llm(monkeypatch, '{"verdict":"verified","confidence":"high","rationale":"looks right"}')
    monkeypatch.setattr(nodes, "run_counterfactual",
                        lambda *a, **k: {"verdict": "not_resolved", "detail": "still fails"})
    monkeypatch.setattr(get_settings(), "copilot_sandbox_verify", True)
    node = make_verify_node()
    state = {
        "messages": [HumanMessage(content="why?"),
                     _pr_call(title="Guard null in applyDiscount",
                              patch="--- a/checkout.js\n+++ b/checkout.js\n")],
        "report": dict(_CAUSE_REPORT),
        "verify_attempts": 0,
    }
    out = node(state)
    assert out["status"] == "investigating"  # bounced back to revise
    assert out["report"]["verification"]["verdict"] == "unverified"
    assert out["report"]["verification"]["sandbox"]["verdict"] == "not_resolved"


def test_verify_node_no_fix_finalizes_without_calling_llm(monkeypatch):
    from app.graph import nodes
    from app.graph.nodes import make_verify_node

    class _BoomLLM:
        def invoke(self, _msgs):
            raise AssertionError("LLM must not be called when there is no fix to verify")

    monkeypatch.setattr(nodes, "get_llm", lambda fast=False: _BoomLLM())
    node = make_verify_node()
    state = {
        "messages": [HumanMessage(content="which services exist?")],
        "report": {"root_cause": None, "recommended_actions": []},
    }
    out = node(state)
    assert out["status"] == "done"
    assert out["report"]["verification"]["verdict"] == "no_fix_proposed"


# --- adversarial RCA critique --------------------------------------------- #
from app.graph.nodes import (  # noqa: E402
    _apply_critique,
    _judge_critique,
    _parse_objections,
    _parse_rebuttals,
)


def test_parse_objections_and_rebuttals():
    objs = _parse_objections('{"objections":[{"claim":"correlation not causation","severity":"high"},'
                             '{"claim":"minor","severity":"bogus"}]}')
    assert objs[0]["severity"] == "high"
    assert objs[1]["severity"] == "low"  # bad enum coerced
    reb = _parse_rebuttals('{"rebuttals":[{"objection":"x","rebutted":true,"evidence":"log line"}]}')
    assert reb[0]["rebutted"] is True


def test_judge_critique_refuted_on_standing_high():
    objs = [{"claim": "wrong service", "severity": "high"}]
    reb = [{"objection": "wrong service", "rebutted": False}]
    assert _judge_critique(objs, reb)["verdict"] == "refuted"


def test_judge_critique_upheld_when_all_rebutted():
    objs = [{"claim": "a", "severity": "high"}, {"claim": "b", "severity": "medium"}]
    reb = [{"rebutted": True}, {"rebutted": True}]
    assert _judge_critique(objs, reb)["verdict"] == "upheld"


def test_judge_critique_missing_rebuttal_counts_as_standing():
    objs = [{"claim": "a", "severity": "medium"}]
    assert _judge_critique(objs, [])["verdict"] == "weakened"  # no rebuttal -> stands


def test_apply_critique_refuted_abstains():
    r = {"calibrated_confidence": "high", "root_cause": "x"}
    out = _apply_critique(r, {"verdict": "refuted",
                              "standing_objections": [{"claim": "unruled-out cause", "severity": "high"}]})
    assert out["abstained"] is True
    assert out["calibrated_confidence"] == "low"
    assert any("Adversarial" in n for n in out["needs"])


def test_apply_critique_weakened_downgrades_one_notch():
    out = _apply_critique({"calibrated_confidence": "high"}, {"verdict": "weakened", "standing_objections": []})
    assert out["calibrated_confidence"] == "medium"


def test_apply_critique_upheld_leaves_confidence():
    out = _apply_critique({"calibrated_confidence": "high"}, {"verdict": "upheld", "standing_objections": []})
    assert out["calibrated_confidence"] == "high"
    assert out.get("abstained") is not True


def test_adversarial_critique_refutes_when_defender_cannot_rebut():
    from app.graph.nodes import _adversarial_critique

    calls: list[int] = []

    class _LLM:
        def invoke(self, _msgs):
            calls.append(1)
            if len(calls) == 1:  # prosecutor
                return SimpleNamespace(
                    content='{"objections":[{"claim":"an alternative cause was not ruled out","severity":"high"}]}',
                    usage_metadata={"total_tokens": 3})
            return SimpleNamespace(  # defender
                content='{"rebuttals":[{"objection":"an alternative cause was not ruled out","rebutted":false,"evidence":"no data"}]}',
                usage_metadata={"total_tokens": 2})

    out = _adversarial_critique(_LLM(), "why?", {"root_cause": "X", "summary": "s"}, "digest")
    assert out["verdict"] == "refuted"
    assert out["tokens"] == 5
    assert len(calls) == 2


def test_adversarial_critique_upheld_and_skips_defender_when_no_objections():
    from app.graph.nodes import _adversarial_critique

    calls: list[int] = []

    class _LLM:
        def invoke(self, _msgs):
            calls.append(1)
            return SimpleNamespace(content='{"objections":[]}', usage_metadata={"total_tokens": 4})

    out = _adversarial_critique(_LLM(), "why?", {"root_cause": "X"}, "digest")
    assert out["verdict"] == "upheld"
    assert len(calls) == 1  # defender not called when nothing to refute


# --- parallel multi-hypothesis probe -------------------------------------- #
from app.graph.nodes import (  # noqa: E402
    _competing_hypotheses,
    _merge_probe_scores,
    _parse_probe,
    _probe_hypotheses,
)


def test_parse_probe_clamps_and_defaults():
    p = _parse_probe('{"support":1.5,"verdict":"maybe","rationale":"x"}')
    assert p["support"] == 1.0 and p["verdict"] == "inconclusive"
    assert _parse_probe("not json")["support"] == 0.0


def test_competing_hypotheses_excludes_invalidated():
    hyps = [{"verdict": "validated"}, {"verdict": "invalidated"}, {"verdict": "inconclusive"}]
    assert len(_competing_hypotheses(hyps)) == 2


def test_merge_probe_scores_reranks_and_sinks_unprobed():
    hyps = [{"cause": "a"}, {"cause": "b"}, {"cause": "c"}]
    scores = {0: {"support": 0.2, "verdict": "inconclusive", "rationale": ""},
              1: {"support": 0.8, "verdict": "validated", "rationale": "r"}}
    merged = _merge_probe_scores(hyps, scores)
    assert [h["cause"] for h in merged] == ["b", "a", "c"]  # 0.8, 0.2, unprobed last
    assert merged[0]["probe_support"] == 0.8


def test_probe_hypotheses_reranks_by_concurrent_support():
    hyps = [
        {"cause": "downstream inventory-svc outage", "verdict": "inconclusive"},
        {"cause": "null deref in applyDiscount", "verdict": "inconclusive"},
    ]

    class _LLM:
        def invoke(self, msgs):
            text = msgs[-1].content
            if "applyDiscount" in text:  # unique to the null-deref hypothesis's cause
                return SimpleNamespace(
                    content='{"support":0.9,"verdict":"validated","rationale":"log matches"}',
                    usage_metadata={"total_tokens": 2})
            return SimpleNamespace(
                content='{"support":0.1,"verdict":"invalidated","rationale":"no evidence"}',
                usage_metadata={"total_tokens": 2})

    out = _probe_hypotheses(_LLM(), hyps, "digest: TypeError checkout.js:42 rising 5xx")
    assert out["probed"] == 2
    assert out["tokens"] == 4
    assert out["hypotheses"][0]["cause"] == "null deref in applyDiscount"
    assert out["hypotheses"][0]["probe_support"] == 0.9


def test_probe_hypotheses_noop_with_fewer_than_two_competing():
    hyps = [{"cause": "a", "verdict": "validated"}, {"cause": "b", "verdict": "invalidated"}]
    out = _probe_hypotheses(object(), hyps, "d")  # only 1 competing -> llm never touched
    assert out["probed"] == 0
    assert out["hypotheses"] == hyps
