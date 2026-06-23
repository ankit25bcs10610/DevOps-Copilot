"""Lightweight eval harness.

Runs each case in testcases.yaml through a real agent session, then scores:
  - keyword recall: did the final answer mention the expected root-cause signals?
  - tool usage:     did the agent call the expected categories of tools?
  - latency:        wall-clock per case.

Auto-approves any write action so runs are non-interactive.

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

from app.session import CopilotSession

console = Console()
CASES_FILE = Path(__file__).parent / "testcases.yaml"


def _tools_used(trace: list[str]) -> set[str]:
    """Extract tool names mentioned in the run trace."""
    used: set[str] = set()
    for line in trace:
        if "calling tool(s):" in line:
            names = line.split("calling tool(s):", 1)[1]
            used.update(n.strip() for n in names.split(","))
    return used


async def _run_case(case: dict) -> dict:
    start = time.perf_counter()
    full_trace: list[str] = []
    # Unique thread id per run so a persistent checkpointer never resumes stale
    # state from a previous (or crashed) eval run.
    thread_id = f"eval-{case['name']}-{uuid.uuid4().hex[:8]}"
    async with CopilotSession(thread_id=thread_id) as session:
        result = await session.ask(case["question"])
        full_trace += result.trace
        # Auto-approve any pending write so evals are non-interactive.
        while result.status == "awaiting_approval":
            result = await session.resume(approved=True, reason="auto-approved in eval")
            full_trace += result.trace
        answer = result.final_text

    elapsed = time.perf_counter() - start
    answer_l = answer.lower()

    kw_hits = [k for k in case["expect_keywords"] if k.lower() in answer_l]
    kw_score = len(kw_hits) / len(case["expect_keywords"])

    used = _tools_used(full_trace)
    tool_ok = bool(set(case["expect_tools_any"]) & used) and bool(
        set(case["expect_tools_any_2"]) & used
    )

    passed = kw_score >= 0.5 and tool_ok
    return {
        "name": case["name"],
        "passed": passed,
        "keyword_recall": kw_score,
        "tools_ok": tool_ok,
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
    table.add_column("latency")
    for r in results:
        table.add_row(
            r["name"],
            "✅" if r["passed"] else "❌",
            f"{r['keyword_recall']:.0%}",
            "✅" if r["tools_ok"] else "❌",
            f"{r['latency_s']}s",
        )
    console.print(table)

    passed = sum(r["passed"] for r in results)
    console.print(f"\n[bold]{passed}/{len(results)} passed[/bold]")


if __name__ == "__main__":
    asyncio.run(main())
