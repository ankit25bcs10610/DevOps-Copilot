"""Interactive CLI for DevOps Copilot.

Usage:
    copilot                       # interactive REPL
    copilot "why are checkouts 500ing?"   # one-shot question

Approval prompts appear inline when the agent wants to perform a write action.
"""

from __future__ import annotations

import asyncio
import sys

from rich.console import Console
from rich.markdown import Markdown
from rich.panel import Panel

from app.config import get_settings
from app.llm import active_api_key
from app.session import CopilotSession, TurnResult

console = Console()


def _show_trace(result: TurnResult) -> None:
    for line in result.trace:
        if line.startswith("•") or "__interrupt__" in line:
            continue
        console.print(f"  [dim]{line}[/dim]")


async def _handle(session: CopilotSession, result: TurnResult) -> None:
    """Print a turn result, looping through approvals until completion."""
    while result.status == "awaiting_approval":
        _show_trace(result)
        req = result.approval_request or {}
        actions = req.get("actions", [])
        body = "\n".join(
            f"[bold]{a['tool']}[/bold]\n{a['args']}" for a in actions
        )
        console.print(
            Panel(body, title="⏸️  Approval required", border_style="yellow")
        )
        answer = console.input("[bold yellow]Approve this action? [y/N]: [/]").strip().lower()
        approved = answer in {"y", "yes"}
        reason = "" if approved else console.input("Reason (optional): ").strip()
        result = await session.resume(approved=approved, reason=reason)

    _show_trace(result)
    console.print()
    console.print(Panel(Markdown(result.final_text), title="✅ DevOps Copilot", border_style="green"))


async def _run(question: str | None) -> None:
    settings = get_settings()
    if not active_api_key():
        key = "ANTHROPIC_API_KEY" if settings.copilot_provider == "anthropic" else "GROQ_API_KEY"
        console.print(
            f"[red]{key} is not set (COPILOT_PROVIDER={settings.copilot_provider}). "
            "Copy .env.example to .env and add your key.[/red]"
        )
        sys.exit(1)

    async with CopilotSession(thread_id="cli") as session:
        if question:
            with console.status("[cyan]investigating…[/cyan]"):
                result = await session.ask(question)
            await _handle(session, result)
            return

        console.print("[bold cyan]DevOps Copilot[/bold cyan] — ask about an incident. Ctrl-C to quit.\n")
        while True:
            try:
                q = console.input("[bold]you ›[/] ").strip()
            except (EOFError, KeyboardInterrupt):
                console.print("\nbye 👋")
                return
            if not q:
                continue
            with console.status("[cyan]investigating…[/cyan]"):
                result = await session.ask(q)
            await _handle(session, result)
            console.print()


def main() -> None:
    question = " ".join(sys.argv[1:]) or None
    asyncio.run(_run(question))


if __name__ == "__main__":
    main()
