"""Golden-trajectory regression gate.

Runs the eval cases OFFLINE against recorded cassettes (no LLM key), failing if the
agent's tool use or structured RCA verdict regresses — the safety net for prompt /
model / ranker / tool changes that the thumbs-feedback loop only catches after
users complain.

Two-step workflow:

  1. Record once, with a live LLM key (the MCP servers already run deterministic
     offline fixtures, so only the LLM is recorded):
         uv run python -m evals.run_golden --record

  2. Replay forever, offline, in CI (no key needed):
         uv run python -m evals.run_golden

In replay mode a regression (a case that no longer passes) exits non-zero so CI
fails. If no cassette exists yet it exits 0 with a notice — nothing to gate.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from pathlib import Path

import yaml
from rich.console import Console
from rich.table import Table

console = Console()
DEFAULT_CASSETTE = "evals/cassettes/golden.json"


def main() -> None:
    parser = argparse.ArgumentParser(description="Golden-trajectory replay eval gate")
    parser.add_argument("--record", action="store_true",
                        help="record cassettes from a live LLM (needs an API key)")
    parser.add_argument("--cassette", default=os.environ.get("COPILOT_CASSETTE_PATH", DEFAULT_CASSETTE))
    args = parser.parse_args()

    os.environ["COPILOT_REPLAY_MODE"] = "record" if args.record else "replay"
    os.environ["COPILOT_CASSETTE_PATH"] = args.cassette

    # Import after setting env so the replay layer + session pick up the mode.
    from app import replay
    from evals.run_evals import _run_case

    replay.reset_cassette()

    cassette = Path(args.cassette)
    if not args.record and not cassette.exists():
        console.print(
            f"[yellow]No cassette at {cassette}. Record one first:\n"
            f"  COPILOT_REPLAY_MODE=record uv run python -m evals.run_golden --record[/yellow]"
        )
        sys.exit(0)  # nothing to gate yet — not a failure

    cases = yaml.safe_load((Path(__file__).parent / "testcases.yaml").read_text())
    mode = "RECORD" if args.record else "REPLAY"
    console.print(f"[bold]{mode}: {len(cases)} golden case(s) → {cassette}[/bold]\n")

    results = []
    for case in cases:
        console.print(f"  ▶ {case['name']}")
        try:
            results.append(asyncio.run(_run_case(case)))
        except Exception as exc:  # noqa: BLE001 — a crash is a failed case, not a harness crash
            results.append({
                "name": case["name"], "passed": False, "keyword_recall": 0.0,
                "tools_ok": False, "report_ok": False, "latency_s": 0.0, "error": str(exc),
            })

    table = Table(title=f"Golden eval ({mode.lower()})")
    for col in ("case", "pass", "kw recall", "tools ok", "report ok", "latency"):
        table.add_column(col)
    for r in results:
        table.add_row(
            r["name"], "✅" if r["passed"] else "❌", f"{r['keyword_recall']:.0%}",
            "✅" if r["tools_ok"] else "❌", "✅" if r["report_ok"] else "❌", f"{r['latency_s']}s",
        )
    console.print(table)

    passed = sum(r["passed"] for r in results)
    console.print(f"\n[bold]{passed}/{len(results)} passed[/bold]")
    if args.record:
        console.print(f"[green]Recorded cassette: {cassette} ({len(replay._cassette())} entries)[/green]")

    # In replay mode, any regression fails CI.
    if not args.record and passed < len(results):
        console.print(f"[red]{len(results) - passed} regression(s) — failing the gate.[/red]")
        sys.exit(1)


if __name__ == "__main__":
    main()
