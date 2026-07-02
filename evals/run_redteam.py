"""Prompt-injection red-team harness — score app/guardrails.py against a corpus of
indirect-injection attacks + benign look-alikes.

Fully offline and deterministic (the guardrails are regex): no LLM, no network.
Prints a report and exits non-zero when detection drops below / false-positives
rise above the thresholds, so it can gate CI next to the golden replay set.

    python -m evals.run_redteam          # score + gate with defaults
    python -m evals.run_redteam --json   # machine-readable summary
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import yaml

from app import guardrails

CORPUS = Path(__file__).parent / "redteam_corpus.yaml"

# Gate thresholds. The curated corpus is designed to be fully separable, so we
# demand perfect scores; loosen only with a deliberate, reviewed reason.
MIN_DETECTION = 1.0  # recall on attacks (fraction flagged)
MAX_FALSE_POSITIVE = 0.0  # fraction of benign inputs wrongly flagged


def load_corpus(path: Path = CORPUS) -> list[dict]:
    return yaml.safe_load(path.read_text()) or []


def score_case(case: dict) -> dict:
    """Run the guardrails on one case and judge it. Pure over `case`."""
    flags = guardrails.scan_for_injection(case.get("text", ""))
    attack = bool(case.get("attack"))
    flagged = bool(flags)
    if attack:
        expected = set(case.get("expect_labels") or [])
        # Correct = detected at all; labels are reported for diagnostics.
        return {
            "name": case.get("name"),
            "attack": True,
            "flagged": flagged,
            "correct": flagged,
            "flags": flags,
            "missing_labels": sorted(expected - set(flags)),
        }
    return {
        "name": case.get("name"),
        "attack": False,
        "flagged": flagged,
        "correct": not flagged,  # benign must be clean
        "flags": flags,
        "missing_labels": [],
    }


def summarize(cases: list[dict]) -> dict:
    """Aggregate per-case results into detection / false-positive rates."""
    results = [score_case(c) for c in cases]
    attacks = [r for r in results if r["attack"]]
    benign = [r for r in results if not r["attack"]]
    detected = sum(1 for r in attacks if r["flagged"])
    false_pos = sum(1 for r in benign if r["flagged"])
    detection_rate = detected / len(attacks) if attacks else 1.0
    fp_rate = false_pos / len(benign) if benign else 0.0
    return {
        "results": results,
        "attacks": len(attacks),
        "benign": len(benign),
        "detected": detected,
        "false_positives": false_pos,
        "detection_rate": round(detection_rate, 3),
        "false_positive_rate": round(fp_rate, 3),
        "passed": detection_rate >= MIN_DETECTION and fp_rate <= MAX_FALSE_POSITIVE,
    }


def main() -> int:
    ap = argparse.ArgumentParser(description="Prompt-injection red-team gate")
    ap.add_argument("--json", action="store_true", help="emit a JSON summary")
    args = ap.parse_args()

    summary = summarize(load_corpus())
    if args.json:
        print(json.dumps(summary, indent=2))
        return 0 if summary["passed"] else 1

    print("Prompt-injection red-team\n" + "=" * 48)
    for r in summary["results"]:
        mark = "✓" if r["correct"] else "✗"
        kind = "attack" if r["attack"] else "benign"
        detail = f"flags={r['flags']}" if r["flags"] else "clean"
        print(f"  {mark} [{kind}] {r['name']}: {detail}")
        if r["missing_labels"]:
            print(f"      (note: expected-but-missing labels {r['missing_labels']})")
    print("-" * 48)
    print(
        f"detection rate     : {summary['detection_rate']:.0%} "
        f"({summary['detected']}/{summary['attacks']} attacks)"
    )
    print(
        f"false-positive rate: {summary['false_positive_rate']:.0%} "
        f"({summary['false_positives']}/{summary['benign']} benign)"
    )
    print(("PASS ✅" if summary["passed"] else "FAIL ❌") + " (gate)")
    return 0 if summary["passed"] else 1


if __name__ == "__main__":
    sys.exit(main())
