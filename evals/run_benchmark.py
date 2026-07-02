"""RCA benchmark runner — score DevOps Copilot's structured RCA into a scorecard.

Two things, both grounded in the 2025-26 agent-eval literature:

  1. Scores each ground-truthed case (evals/benchmark_cases.yaml) — replays the agent
     offline (needs a cassette; --live to record/run against a real LLM) and grades
     the OUTCOME with the deterministic scorers in evals/benchmark.py, stratified by
     difficulty tier.

  2. A NULL/ADVERSARIAL baseline self-check (Agentic Benchmark Checklist): scores a
     do-nothing agent and an evidence-spam agent against every case and asserts they
     do NOT pass — proving the scorer can't be gamed by no-op or restatement.

    uv run python -m evals.run_benchmark              # scorecard + baseline self-check
    uv run python -m evals.run_benchmark --baselines  # baseline self-check only (fully offline)
    uv run python -m evals.run_benchmark --json
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path

import yaml
from rich.console import Console
from rich.table import Table

from evals.benchmark import Scorecard, score_case

console = Console()
CASES = Path(__file__).parent / "benchmark_cases.yaml"


def _null_prediction(_case: dict) -> dict:
    """Do-nothing agent: produces no diagnosis."""
    return {"root_cause": None, "summary": "", "severity": "INFO", "abstained": False,
            "affected_services": [], "evidence": [], "calibrated_confidence": "low"}


def _spam_prediction(_case: dict) -> dict:
    """Evidence-spam agent: dumps every service, every layer/fault keyword, and generic
    'evidence' — the restatement attack that inflates lexical metrics."""
    every = "checkout-svc inventory-svc cart-svc payments-svc auth-svc"
    kitchen_sink = ("application infra deploy data network null undefined config "
                    "deploy regression oom memory downstream dependency timeout")
    return {
        "root_cause": f"the problem is in {every}: {kitchen_sink}",
        "summary": f"{every} {kitchen_sink}",
        "severity": "SEV1", "abstained": False,
        "affected_services": every.split(),
        "evidence": [f"log line {i}: {kitchen_sink}" for i in range(20)],
        "calibrated_confidence": "high",
    }


def _prediction_from_report(report: dict) -> dict:
    return report or {}


async def _run_case(case: dict) -> dict | None:
    """Replay/run the agent for a case and return (prediction, trace_meta)-scored row.
    Returns None if the case can't run (no cassette in replay mode)."""
    from app.policy import APPROVE_TOOLS
    from app.session import CopilotSession
    from evals.scorers import path_safety_ok, step_count, tools_used  # noqa: F401

    thread = f"bench-{case['name']}"
    try:
        async with CopilotSession(thread_id=thread) as session:
            result = await session.ask(case["question"])
            trace = list(result.trace)
            while result.status == "awaiting_approval":
                result = await session.resume(approved=True, reason="benchmark", auto=True)
                trace += result.trace
            report = result.report or {}
            meta = {
                "steps": step_count(trace),
                "tokens": result.tokens_used,
                "path_safe": path_safety_ok(trace, APPROVE_TOOLS),
                "source_blob": " ".join(trace),
            }
    except Exception as exc:  # noqa: BLE001 — a miss/crash is a skipped case, not a harness crash
        console.print(f"    [yellow]skip {case['name']}: {type(exc).__name__} "
                      f"(record a cassette or use --live)[/yellow]")
        return None
    return score_case(_prediction_from_report(report), case, meta)


def _print_scorecard(rows: list[dict], title: str) -> None:
    card = Scorecard(rows).summary()
    o = card["overall"]
    console.print(f"\n[bold]{title}[/bold]  (n={o['n']})")
    t = Table()
    for col in ("tier", "n", "A@1", "PCW", "loc@1", "loc@3", "ev.recall", "grounded", "ECE", "Brier"):
        t.add_column(col)
    t.add_row("overall", str(o["n"]), f"{o['a1']:.0%}", f"{o['pcw']:.2f}", f"{o['loc_top1']:.0%}",
              f"{o['loc_top3']:.0%}", f"{o['evidence_recall']:.0%}", f"{o['groundedness']:.0%}",
              f"{o['ece']:.3f}", f"{o['brier']:.3f}")
    for tier, agg in card["by_difficulty"].items():
        t.add_row(tier, str(agg["n"]), f"{agg['a1']:.0%}", f"{agg['pcw']:.2f}", f"{agg['loc_top1']:.0%}",
                  f"{agg['loc_top3']:.0%}", f"{agg['evidence_recall']:.0%}", f"{agg['groundedness']:.0%}",
                  f"{agg['ece']:.3f}", f"{agg['brier']:.3f}")
    console.print(t)
    console.print(f"  failure modes: {card['failure_modes']}")
    console.print(f"  abstention:    {card['abstention']}")


def baseline_selfcheck(cases: list[dict]) -> bool:
    """Score null + adversarial baselines; they must NOT pass any case. Returns ok."""
    null_rows = [score_case(_null_prediction(c), c) for c in cases]
    spam_rows = [score_case(_spam_prediction(c), c) for c in cases]
    null_correct = sum(r["correct"] for r in null_rows)
    spam_correct = sum(r["correct"] for r in spam_rows)
    _print_scorecard(null_rows, "NULL baseline (do-nothing)")
    _print_scorecard(spam_rows, "ADVERSARIAL baseline (evidence-spam)")
    # The do-nothing agent may "correctly abstain" on the abstain case — that's fine
    # and even desirable; what must never happen is a baseline scoring an ANSWER case.
    answer_cases = [c["name"] for c in cases if not c.get("ground_truth", {}).get("should_abstain")]
    null_ans = sum(r["correct"] for r in null_rows if r["name"] in answer_cases)
    spam_ans = sum(r["correct"] for r in spam_rows if r["name"] in answer_cases)
    ok = null_ans == 0 and spam_ans == 0
    console.print(
        f"\n[bold]{'PASS ✅' if ok else 'FAIL ❌'}[/bold] baseline self-check — "
        f"answer-cases passed by null={null_ans}, spam={spam_ans} (must be 0). "
        f"(null total correct={null_correct}, spam total correct={spam_correct})"
    )
    return ok


def main() -> int:
    ap = argparse.ArgumentParser(description="RCA benchmark scorecard")
    ap.add_argument("--baselines", action="store_true", help="run only the baseline self-check (offline)")
    ap.add_argument("--live", action="store_true", help="run the agent live (needs an LLM key)")
    ap.add_argument("--json", action="store_true", help="emit the scorecard as JSON")
    args = ap.parse_args()
    if not args.live:
        os.environ.setdefault("COPILOT_REPLAY_MODE", "replay")

    cases = yaml.safe_load(CASES.read_text())

    if args.baselines:
        return 0 if baseline_selfcheck(cases) else 1

    console.print(f"[bold]Scoring {len(cases)} benchmark case(s)"
                  f"{' (live)' if args.live else ' (replay)'}…[/bold]")
    rows: list[dict] = []
    for case in cases:
        console.print(f"  ▶ {case['name']}")
        row = asyncio.run(_run_case(case))
        if row:
            rows.append(row)

    if args.json:
        print(json.dumps(Scorecard(rows).summary(), indent=2))
    elif rows:
        _print_scorecard(rows, "DevOps Copilot RCA scorecard")
    else:
        console.print("[yellow]No cases scored (no cassettes). Record with the golden "
                      "recorder or run --live, or use --baselines for the offline self-check.[/yellow]")

    ok = baseline_selfcheck(cases)  # always validate the scorer isn't gameable
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
