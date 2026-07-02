"""RCA benchmark scorers + scorecard (pure, deterministic — no LLM)."""

from evals.benchmark import (
    Scorecard,
    abstention_outcome,
    brier,
    canon,
    detection_ok,
    ece,
    evidence_recall,
    failure_mode,
    groundedness,
    localization_topk,
    pass_at_k,
    pass_pow_k,
    score_case,
    score_rca,
)

_CHECKOUT_TRUTH = {"component": "checkout-svc", "layer": "application", "fault_type": "null-dereference"}


def test_canon_normalizes():
    assert canon("  Checkout-SVC \n TypeError ") == "checkout-svc typeerror"


def test_score_rca_all_match_with_synonyms():
    pred = {"root_cause": "undefined coupon caused a TypeError in applyDiscount",
            "summary": "checkout-svc 5xx from an application code bug",
            "affected_services": ["checkout-svc"], "evidence": []}
    r = score_rca(pred, _CHECKOUT_TRUTH)
    assert r["component_match"] and r["layer_match"] and r["fault_match"]
    assert r["a1"] is True
    assert r["pcw"] == 1.0
    assert r["n_elements"] == 3


def test_score_rca_partial_credit():
    pred = {"root_cause": "something happened in checkout-svc", "summary": "", "affected_services": []}
    r = score_rca(pred, _CHECKOUT_TRUTH)
    assert r["component_match"] and not r["fault_match"]
    assert r["a1"] is False
    assert r["pcw"] == 0.5  # component only (0.5)


def test_score_rca_only_scores_specified_elements():
    r = score_rca({"root_cause": "checkout-svc"}, {"component": "checkout-svc"})
    assert r["a1"] is True and r["n_elements"] == 1


def test_localization_topk():
    ranked = ["inventory-svc", "checkout-svc", "cart-svc"]
    r = localization_topk(ranked, "checkout-svc")
    assert r["rank"] == 1 and r["top1"] is False and r["top3"] is True
    assert localization_topk(["checkout-svc"], "checkout-svc")["top1"] is True
    assert localization_topk(["a", "b", "c", "checkout-svc"], "checkout-svc")["top3"] is False


def test_evidence_recall():
    assert evidence_recall(["TypeError at checkout.js:12", "applyDiscount"],
                           ["checkout.js", "applyDiscount", "TypeError"]) == 1.0
    assert evidence_recall(["nothing relevant"], ["checkout.js"]) == 0.0
    assert evidence_recall([], []) == 1.0  # nothing required


def test_groundedness_flags_fabrication():
    src = "ERROR checkout-svc TypeError applyDiscount checkout.js:12 rising 5xx"
    assert groundedness(["TypeError in applyDiscount at checkout.js:12"], src) == 1.0
    assert groundedness(["redis eviction storm overwhelmed the kafka cluster"], src) == 0.0
    assert groundedness([], src) == 1.0  # nothing cited -> nothing fabricated


def test_detection_ok():
    assert detection_ok("SEV2", is_incident=True) is True
    assert detection_ok("INFO", is_incident=True) is False
    assert detection_ok("INFO", is_incident=False) is True


def test_abstention_outcome():
    assert abstention_outcome(True, True) == "correct_abstain"
    assert abstention_outcome(False, True) == "missed_abstain"   # confident-wrong risk
    assert abstention_outcome(True, False) == "over_abstain"
    assert abstention_outcome(False, False) == "answered"


def test_failure_mode_taxonomy():
    strong = score_rca({"root_cause": "null deref in applyDiscount at checkout-svc, application code"},
                       _CHECKOUT_TRUTH)
    assert failure_mode(strong, grounded_ok=True, abstained=False, should_abstain=False) == "precise"
    assert failure_mode(strong, grounded_ok=False, abstained=False, should_abstain=False) == "hallucination"
    assert failure_mode(strong, grounded_ok=True, abstained=True, should_abstain=True) == "insufficient_evidence"
    weak = score_rca({"root_cause": "checkout-svc had an issue"}, _CHECKOUT_TRUTH)
    assert failure_mode(weak, grounded_ok=True, abstained=False, should_abstain=False) == "imprecise"
    none = score_rca({"root_cause": "unrelated"}, _CHECKOUT_TRUTH)
    assert failure_mode(none, grounded_ok=True, abstained=False, should_abstain=False) == "reasoning_error"


def test_pass_at_k_and_pow_k():
    # 2 of 4 samples correct.
    assert pass_at_k(4, 2, 1) == 0.5
    assert pass_at_k(4, 2, 2) > 0.5          # at-least-one rises with k
    assert pass_at_k(4, 4, 3) == 1.0
    assert pass_pow_k(4, 2, 1) == 0.5
    assert pass_pow_k(4, 2, 2) < 0.5          # all-succeed falls with k
    assert pass_pow_k(4, 1, 2) == 0.0         # can't have 2 successes from 1
    assert pass_pow_k(4, 4, 4) == 1.0


def test_calibration_ece_brier():
    # Perfectly calibrated-ish: high confidence + correct.
    assert brier(["high", "high"], [True, True]) < brier(["high", "high"], [False, False])
    # ECE zero-ish when confidence matches accuracy bucket; large when miscalibrated.
    good = ece(["high"], [True])
    bad = ece(["high"], [False])
    assert bad > good


def test_score_case_and_scorecard():
    good = {
        "name": "checkout_500", "difficulty": "easy",
        "ground_truth": {**_CHECKOUT_TRUTH, "artifacts": ["checkout.js", "applyDiscount"]},
    }
    pred = {
        "root_cause": "null deref reading coupon.total in applyDiscount (checkout.js) — application code",
        "summary": "checkout-svc 5xx", "severity": "SEV2", "calibrated_confidence": "high",
        "abstained": False, "affected_services": ["checkout-svc"],
        "evidence": ["TypeError applyDiscount checkout.js:12"],
    }
    row = score_case(pred, good, trace_meta={"steps": 4, "tokens": 1200, "path_safe": True,
                                             "source_blob": "TypeError applyDiscount checkout.js:12"})
    assert row["a1"] and row["correct"] and row["failure_mode"] == "precise"
    assert row["detection_ok"] and row["loc_top1"]

    card = Scorecard([row]).summary()
    assert card["overall"]["n"] == 1
    assert card["overall"]["a1"] == 1.0
    assert card["by_difficulty"]["easy"]["n"] == 1
    assert card["failure_modes"]["precise"] == 1


def test_scorecard_rewards_correct_abstention():
    case = {"name": "thin", "difficulty": "hard",
            "ground_truth": {"component": "checkout-svc", "should_abstain": True}}
    pred = {"root_cause": None, "severity": "SEV3", "abstained": True,
            "calibrated_confidence": "low", "affected_services": [], "evidence": []}
    row = score_case(pred, case)
    assert row["abstention"] == "correct_abstain"
    assert row["correct"] is True  # correct abstention counts as correct
    assert row["failure_mode"] == "insufficient_evidence"


# --- non-gameability (Agentic Benchmark Checklist) ------------------------- #
def test_null_and_spam_baselines_cannot_pass_answer_cases():
    import yaml

    from evals.run_benchmark import CASES, _null_prediction, _spam_prediction, baseline_selfcheck
    cases = yaml.safe_load(CASES.read_text())
    # The self-check asserts neither baseline passes any ANSWER case.
    assert baseline_selfcheck(cases) is True

    # Spot-check the hardest gaming attempt directly on an answer case.
    ans = next(c for c in cases if not c.get("ground_truth", {}).get("should_abstain")
               and c.get("ground_truth", {}).get("component"))
    assert score_case(_null_prediction(ans), ans)["correct"] is False
    spam_row = score_case(_spam_prediction(ans), ans)
    assert spam_row["correct"] is False          # committed-answer scoring defeats restatement
    assert spam_row["a1"] is False               # can't claim every layer/fault and win
