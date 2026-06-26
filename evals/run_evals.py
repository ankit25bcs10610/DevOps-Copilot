"""Lightweight eval harness.

Runs each case in testcases.yaml through a real agent session, then scores:
  - keyword recall: did the final answer mention the expected root-cause signals?
  - tool usage:     did the agent call the expected categories of tools?
  - report verdict: did the structured RCA name the root cause + a valid severity?
  - latency:        wall-clock per case.

Auto-approves any write action so runs are non-interactive. Thumbs-down feedback
captured in production (app/feedback.py, feedback.jsonl) is the natural source of
new regression cases to append here.

Usage:
    uv run python -m evals.run_evals
"""

from __future__ import annotations

import asyncio
import time
import uuid
from pathlib import Path

import yaml
from rich.console import Console
from rich.table import Table

from app.policy import APPROVE_TOOLS
from app.session import CopilotSession
from evals.scorers import path_safety_ok, tools_used

console = Console()
CASES_FILE = Path(__file__).parent / "testcases.yaml"


def _tools_used(trace: list[str]) -> set[str]:
    """Extract tool names mentioned in the run trace (see evals.scorers.tools_used)."""
    return tools_used(trace)


async def _run_case(case: dict) -> dict:
    start = time.perf_counter()
    full_trace: list[str] = []
    # Unique thread id per run so a persistent checkpointer never resumes stale
    # state from a previous (or crashed) eval run.
    thread_id = f"eval-{case['name']}-{uuid.uuid4().hex[:8]}"
    report: dict = {}
    async with CopilotSession(thread_id=thread_id) as session:
        result = await session.ask(case["question"])
        full_trace += result.trace
        # Auto-approve any pending write so evals are non-interactive.
        while result.status == "awaiting_approval":
            result = await session.resume(approved=True, reason="auto-approved in eval")
            full_trace += result.trace
        answer = result.final_text
        report = result.report or {}

    elapsed = time.perf_counter() - start
    answer_l = answer.lower()

    kw_hits = [k for k in case["expect_keywords"] if k.lower() in answer_l]
    kw_score = len(kw_hits) / len(case["expect_keywords"])

    used = _tools_used(full_trace)
    tool_ok = bool(set(case["expect_tools_any"]) & used) and bool(
        set(case["expect_tools_any_2"]) & used
    )

    # Score the structured RCA verdict: the root cause / summary should name at
    # least one expected signal and the severity must be a valid tier.
    report_ok = True
    if case.get("expect_report", True):
        verdict_text = f"{report.get('root_cause') or ''} {report.get('summary') or ''}".lower()
        report_ok = (
            bool(report)
            and any(k.lower() in verdict_text for k in case["expect_keywords"])
            and report.get("severity") in {"SEV1", "SEV2", "SEV3", "SEV4", "INFO"}
        )

    # SAFETY gate: a write must never execute without a preceding approval pause.
    safe = path_safety_ok(full_trace, APPROVE_TOOLS)

    passed = kw_score >= 0.5 and tool_ok and report_ok and safe
    return {
        "name": case["name"],
        "passed": passed,
        "keyword_recall": kw_score,
        "tools_ok": tool_ok,
        "report_ok": report_ok,
        "path_safe": safe,
        "tools_used": sorted(used),
        "latency_s": round(elapsed, 1),
    }


async def main() -> None:
    cases = yaml.safe_load(CASES_FILE.read_text())
    console.print(f"[bold]Running {len(cases)} eval case(s)…[/bold]\n")

    results = []
    for case in cases:
        console.print(f"  ▶ {case['name']}")
        results.append(await _run_case(case))

    table = Table(title="Eval results")
    table.add_column("case")
    table.add_column("pass")
    table.add_column("kw recall")
    table.add_column("tools ok")
    table.add_column("report ok")
    table.add_column("path safe")
    table.add_column("latency")
    for r in results:
        table.add_row(
            r["name"],
            "✅" if r["passed"] else "❌",
            f"{r['keyword_recall']:.0%}",
            "✅" if r["tools_ok"] else "❌",
            "✅" if r["report_ok"] else "❌",
            "✅" if r.get("path_safe") else "❌",
            f"{r['latency_s']}s",
        )
    console.print(table)

    passed = sum(r["passed"] for r in results)
    console.print(f"\n[bold]{passed}/{len(results)} passed[/bold]")


if __name__ == "__main__":
    asyncio.run(main())
