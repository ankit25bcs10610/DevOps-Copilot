"""RCA benchmark scorers + scorecard — the deterministic scoring methodology.

Derived from the 2025-26 agent-evaluation literature (AIOpsLab, OpenRCA, CUJBench,
RCAgent, the Microsoft FSE'24 RCA study, the Agentic Benchmark Checklist, and
Anthropic's evals guidance). Design choices, and why:

  - Score the structured RCA against ground-truth *elements* (component / layer /
    fault-type) with exact-match after canonicalization — NOT BLEU/ROUGE/BERTScore,
    which can't measure factual accuracy and are inflated by generic restatements.
  - Report all-or-nothing A@1 AND partial-credit-weighted PCW (0.5/0.2/0.3), plus
    localization top-1/top-3 over the ranked hypotheses.
  - Stratify by difficulty tier (number of required elements) — accuracy collapses
    on multi-element cases, so a single aggregate hides the real story.
  - Grade the OUTCOME, not the exact tool path; keep non-rigid trajectory/efficiency
    signals (steps, path-safety).
  - Evidence: recall of ground-truth artifacts + groundedness (cited evidence must
    fuzzy-match the source telemetry) — deterministic, since LLM-judge citation
    checking is unreliable.
  - Treat abstention as a first-class outcome (reward correct abstention; penalize
    confident-wrong) and measure calibration with ECE + Brier.
  - pass@k / pass^k reliability estimators (Chen et al. / consistency).

Everything here is PURE and unit-tested — no LLM, no network — so the scorecard is
deterministic and offline, like the golden gate it complements.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from math import comb

# --------------------------------------------------------------------------- #
# Canonicalization + element matching
# --------------------------------------------------------------------------- #
_WS = re.compile(r"\s+")


def canon(s: object) -> str:
    """Lowercase, collapse whitespace, strip — the canonical form both sides are
    reduced to before exact-match (OpenRCA/CUJBench style)."""
    return _WS.sub(" ", str(s or "").strip().lower())


# Synonym sets so a correct answer phrased differently still matches (the closed
# answer space, expressed as accepted surface forms per canonical element).
_LAYER_SYNONYMS: dict[str, set[str]] = {
    "application": {"application", "app", "code", "application code", "source", "logic"},
    "infra": {"infra", "infrastructure", "kubernetes", "k8s", "pod", "node", "cluster", "resource"},
    "deploy": {"deploy", "deployment", "release", "rollout", "change", "ci/cd"},
    "data": {"data", "database", "db", "query", "schema", "migration"},
    "network": {"network", "dns", "connectivity", "timeout", "latency", "upstream"},
}
_FAULT_SYNONYMS: dict[str, set[str]] = {
    "null-dereference": {"null", "undefined", "cannot read properties", "nullpointer",
                         "typeerror", "null deref", "null dereference", "none"},
    "config": {"config", "configuration", "misconfiguration", "env", "flag", "setting"},
    "deploy-regression": {"deploy", "regression", "bad deploy", "recent change", "commit",
                          "rollout", "change-induced"},
    "resource-exhaustion": {"oom", "out of memory", "cpu", "memory", "throttle", "limit",
                            "exhaustion", "leak"},
    "dependency-failure": {"downstream", "upstream", "dependency", "outage", "unavailable"},
}


def _contains(haystack: str, needle: str) -> bool:
    """Whole-token containment (word-boundary), so a short synonym like "app" doesn't
    spuriously match inside "happened"."""
    n = canon(needle)
    if not n:
        return False
    return re.search(rf"(?<![a-z0-9]){re.escape(n)}(?![a-z0-9])", haystack) is not None


def _match_element(text: str, truth: str, synonyms: dict[str, set[str]] | None = None) -> bool:
    """True if the canonical truth element (or one of its accepted synonyms) appears
    in the (already-canonicalized) prediction text."""
    if not truth:
        return True  # nothing required
    if _contains(text, truth):
        return True
    forms = (synonyms or {}).get(canon(truth), set())
    return any(_contains(text, f) for f in forms)


# A committed diagnosis names ONE root component, not a laundry list — a real blast
# radius is small; an evidence-spam agent dumps everything. Cap the breadth that still
# counts as a localized answer (ABC-checklist anti-gaming).
_MAX_COMMITTED_SERVICES = 3


def _categories_mentioned(text: str, synonyms: dict[str, set[str]]) -> set[str]:
    """Which canonical categories (layer/fault) the text commits to — a category is
    'mentioned' if its name or any surface form appears as a whole token."""
    hits: set[str] = set()
    for cat, forms in synonyms.items():
        if _contains(text, cat) or any(_contains(text, f) for f in forms):
            hits.add(cat)
    return hits


def score_rca(prediction: dict, truth: dict) -> dict:
    """Score a structured RCA prediction against ground-truth elements.

    prediction: {root_cause, summary, affected_services[], evidence[]} (from the RCA report)
    truth:      {component, layer, fault_type}
    Returns component/layer/fault match bools, A@1 (all match), and PCW (weighted).

    Matching is COMMITTED, not mere mention: layer/fault credit requires the truth
    category AND no competing category (naming every option earns nothing), and
    component credit requires a small, committed blast radius — so an evidence-spam
    baseline that restates all options cannot score (OpenRCA closed-answer principle)."""
    services = prediction.get("affected_services") or []
    blob = canon(" ".join([
        str(prediction.get("root_cause") or ""),
        str(prediction.get("summary") or ""),
        " ".join(services),
        " ".join(str(e) for e in (prediction.get("evidence") or [])),
    ]))
    # Component: named AND the answer is committed (not a dump of every service).
    committed = len(services) <= _MAX_COMMITTED_SERVICES
    component = _match_element(blob, truth.get("component", "")) and committed
    # Layer/fault: the truth category is present AND it is the ONLY category named.
    layer_cats = _categories_mentioned(blob, _LAYER_SYNONYMS)
    fault_cats = _categories_mentioned(blob, _FAULT_SYNONYMS)
    truth_layer, truth_fault = canon(truth.get("layer", "")), canon(truth.get("fault_type", ""))
    layer = bool(truth_layer) and layer_cats == {truth_layer}
    fault = bool(truth_fault) and fault_cats == {truth_fault}
    # Only score elements the ground truth actually specifies.
    required = [k for k in ("component", "layer", "fault_type") if truth.get(k)]
    got = {"component": component, "layer": layer, "fault_type": fault}
    a1 = all(got[k] for k in required) if required else False
    pcw = round(
        0.5 * component * bool(truth.get("component"))
        + 0.2 * layer * bool(truth.get("layer"))
        + 0.3 * fault * bool(truth.get("fault_type")),
        3,
    )
    return {"component_match": component, "layer_match": layer, "fault_match": fault,
            "a1": a1, "pcw": pcw, "n_elements": len(required)}


def localization_topk(ranked: list[str], truth_component: str) -> dict:
    """Top-1 / Top-3 hit of the faulty component among the ranked candidates
    (hypotheses/affected services, best-first)."""
    cands = [canon(r) for r in (ranked or [])]
    tc = canon(truth_component)
    hit_at = next((i for i, c in enumerate(cands) if tc and tc in c), None)
    return {"top1": hit_at == 0, "top3": hit_at is not None and hit_at < 3, "rank": hit_at}


# --------------------------------------------------------------------------- #
# Evidence: recall of ground-truth artifacts + groundedness of citations
# --------------------------------------------------------------------------- #
_TOKEN = re.compile(r"[a-z0-9_./:]{3,}")
_STOP = {"the", "and", "for", "with", "that", "this", "from", "was", "were", "not"}


def _salient(text: str) -> set[str]:
    return {t for t in _TOKEN.findall(canon(text))} - _STOP


def evidence_recall(cited: list[str], truth_artifacts: list[str]) -> float:
    """Fraction of ground-truth artifact identifiers that appear in the cited evidence."""
    if not truth_artifacts:
        return 1.0
    blob = canon(" ".join(str(c) for c in (cited or [])))
    hits = sum(1 for a in truth_artifacts if _contains(blob, a))
    return round(hits / len(truth_artifacts), 3)


def groundedness(cited: list[str], source_blob: str, threshold: float = 0.4) -> float:
    """Fraction of cited evidence items whose salient tokens sufficiently overlap the
    source telemetry — the deterministic anti-hallucination check (RCAgent-style)."""
    items = [c for c in (cited or []) if _salient(str(c))]
    if not items:
        return 1.0  # nothing cited -> nothing fabricated
    src = _salient(source_blob)
    if not src:
        return 0.0
    grounded = 0
    for c in items:
        toks = _salient(str(c))
        if toks and len(toks & src) / len(toks) >= threshold:
            grounded += 1
    return round(grounded / len(items), 3)


# --------------------------------------------------------------------------- #
# Detection, abstention, failure-mode taxonomy
# --------------------------------------------------------------------------- #
def detection_ok(severity: str, is_incident: bool) -> bool:
    """Detection = did the agent flag an incident as an incident (severity != INFO)?
    For a non-incident (informational) case, INFO is the correct detection."""
    flagged = canon(severity) not in ("info", "")
    return flagged == is_incident


def abstention_outcome(abstained: bool, should_abstain: bool) -> str:
    """Classify the abstain decision — rewards correct abstention, flags the
    dangerous confident-wrong case (should have abstained but didn't)."""
    if should_abstain and abstained:
        return "correct_abstain"
    if should_abstain and not abstained:
        return "missed_abstain"       # confident-wrong risk
    if not should_abstain and abstained:
        return "over_abstain"         # unhelpfully cautious
    return "answered"


# Microsoft FSE'24 failure-mode taxonomy (abstention is a distinct label).
def failure_mode(rca: dict, grounded_ok: bool, abstained: bool, should_abstain: bool) -> str:
    if abstained:
        return "insufficient_evidence"
    if not grounded_ok:
        return "hallucination"
    if rca["a1"]:
        return "precise"
    if rca["component_match"]:
        return "imprecise"
    return "reasoning_error"


# --------------------------------------------------------------------------- #
# Reliability (pass@k / pass^k) + calibration (ECE / Brier)
# --------------------------------------------------------------------------- #
def pass_at_k(n: int, c: int, k: int) -> float:
    """Unbiased pass@k (Chen et al. 2021): P(at least one of k sampled attempts is
    correct), estimated from n samples with c correct. Rises with k (capability)."""
    if k <= 0 or n <= 0:
        return 0.0
    k = min(k, n)
    if n - c < k:
        return 1.0
    return round(1.0 - comb(n - c, k) / comb(n, k), 4)


def pass_pow_k(n: int, c: int, k: int) -> float:
    """pass^k: P(all k sampled attempts succeed), estimated from n samples with c
    correct. Falls with k (consistency/reliability). Note: under fully deterministic
    replay n==1 so this collapses to pass^1 — real reliability needs live sampling."""
    if k <= 0 or n <= 0:
        return 0.0
    k = min(k, n)
    if c < k:
        return 0.0
    return round(comb(c, k) / comb(n, k), 4)


_CONF_NUM = {"high": 0.9, "medium": 0.6, "low": 0.3}


def _conf_to_p(conf: str) -> float:
    return _CONF_NUM.get(canon(conf), 0.3)


def brier(confidences: list[str], correct: list[bool]) -> float:
    """Brier score: mean squared error between stated confidence (as a probability)
    and correctness. Lower is better; a proper scoring rule for calibration."""
    if not confidences:
        return 0.0
    return round(sum((_conf_to_p(c) - bool(y)) ** 2 for c, y in zip(confidences, correct))
                 / len(confidences), 4)


def ece(confidences: list[str], correct: list[bool]) -> float:
    """Expected Calibration Error over the discrete confidence buckets: the
    accuracy-weighted gap between mean confidence and empirical accuracy per bucket."""
    if not confidences:
        return 0.0
    buckets: dict[str, list[bool]] = {}
    for c, y in zip(confidences, correct):
        buckets.setdefault(canon(c) or "low", []).append(bool(y))
    n = len(confidences)
    err = 0.0
    for label, ys in buckets.items():
        acc = sum(ys) / len(ys)
        conf = _CONF_NUM.get(label, 0.3)
        err += (len(ys) / n) * abs(acc - conf)
    return round(err, 4)


# --------------------------------------------------------------------------- #
# Per-case scoring + scorecard aggregation
# --------------------------------------------------------------------------- #
def score_case(prediction: dict, case: dict, trace_meta: dict | None = None) -> dict:
    """Score one case end to end. `case` carries ground_truth + difficulty; `prediction`
    is the RCA report; `trace_meta` optionally carries {steps, tokens, path_safe}."""
    gt = case.get("ground_truth", {})
    trace_meta = trace_meta or {}
    rca = score_rca(prediction, gt)
    ranked = list(prediction.get("affected_services") or []) + [
        h.get("cause", "") for h in (prediction.get("hypotheses") or [])
    ]
    loc = localization_topk(ranked, gt.get("component", ""))
    cited = list(prediction.get("evidence") or []) + [
        e for h in (prediction.get("hypotheses") or []) for e in (h.get("evidence") or [])
    ]
    ev_recall = evidence_recall(cited, gt.get("artifacts") or [])
    ground = groundedness(cited, trace_meta.get("source_blob", "") or " ".join(str(c) for c in cited))
    is_incident = not bool(gt.get("informational"))
    detect = detection_ok(prediction.get("severity", ""), is_incident)
    should_abstain = bool(gt.get("should_abstain"))
    abstained = bool(prediction.get("abstained"))
    abst = abstention_outcome(abstained, should_abstain)
    grounded_ok = ground >= 0.5
    # "Correct" for reliability = a correct abstention, OR (answer expected) all RCA
    # elements match AND the faulty component is ranked first (committed localization)
    # AND the citations are grounded. Requiring top-1 + groundedness — not mere mention
    # — is what resists a do-nothing or evidence-spam baseline from scoring (ABC checklist).
    needs_component = bool(gt.get("component"))
    localized = loc["top1"] if needs_component else True
    correct = (abst == "correct_abstain") or (
        not should_abstain and rca["a1"] and localized and grounded_ok
    )
    fmode = failure_mode(rca, grounded_ok, abstained, should_abstain)
    confidence = prediction.get("calibrated_confidence") or prediction.get("confidence") or "low"
    return {
        "name": case.get("name"),
        "difficulty": case.get("difficulty", "unknown"),
        **rca,
        "loc_top1": loc["top1"], "loc_top3": loc["top3"],
        "evidence_recall": ev_recall, "groundedness": ground,
        "detection_ok": detect, "abstention": abst, "failure_mode": fmode,
        "correct": correct, "confidence": confidence,
        "steps": trace_meta.get("steps"), "tokens": trace_meta.get("tokens"),
        "path_safe": trace_meta.get("path_safe", True),
    }


def _mean(xs: list[float]) -> float:
    xs = [x for x in xs if x is not None]
    return round(sum(xs) / len(xs), 3) if xs else 0.0


@dataclass
class Scorecard:
    """Aggregate the per-case rows into an overall + per-difficulty-tier report."""

    rows: list[dict] = field(default_factory=list)

    def _agg(self, rows: list[dict]) -> dict:
        if not rows:
            return {"n": 0}
        return {
            "n": len(rows),
            "a1": _mean([r["a1"] for r in rows]),
            "pcw": _mean([r["pcw"] for r in rows]),
            "loc_top1": _mean([r["loc_top1"] for r in rows]),
            "loc_top3": _mean([r["loc_top3"] for r in rows]),
            "evidence_recall": _mean([r["evidence_recall"] for r in rows]),
            "groundedness": _mean([r["groundedness"] for r in rows]),
            "detection": _mean([r["detection_ok"] for r in rows]),
            "path_safe": _mean([r["path_safe"] for r in rows]),
            "ece": ece([r["confidence"] for r in rows], [r["correct"] for r in rows]),
            "brier": brier([r["confidence"] for r in rows], [r["correct"] for r in rows]),
        }

    def summary(self) -> dict:
        tiers = {}
        for tier in ("easy", "mid", "hard"):
            trows = [r for r in self.rows if r.get("difficulty") == tier]
            if trows:
                tiers[tier] = self._agg(trows)
        fmodes: dict[str, int] = {}
        abst: dict[str, int] = {}
        for r in self.rows:
            fmodes[r["failure_mode"]] = fmodes.get(r["failure_mode"], 0) + 1
            abst[r["abstention"]] = abst.get(r["abstention"], 0) + 1
        return {
            "overall": self._agg(self.rows),
            "by_difficulty": tiers,
            "failure_modes": fmodes,
            "abstention": abst,
        }
