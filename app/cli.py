"""Interactive CLI for DevOps Copilot.

Usage:
    copilot                       # interactive REPL
    copilot "why are checkouts 500ing?"   # one-shot question
    copilot provision-org --name "Acme" --email owner@acme.com [--plan team]

Approval prompts appear inline when the agent wants to perform a write action.
The provision-org subcommand bootstraps a tenant (org + owner API key) in the
tenant store — the out-of-band step before turning on COPILOT_MULTI_TENANT=true.
"""

from __future__ import annotations

import argparse
import asyncio
import sys

from rich.console import Console
from rich.markdown import Markdown
from rich.panel import Panel

from app import observability
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
        key = f"{settings.copilot_provider.upper()}_API_KEY"
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


async def _provision_org(rest: list[str]) -> None:
    """Create an org + its owner API key in the tenant store (commercial bootstrap)."""
    parser = argparse.ArgumentParser(prog="copilot provision-org")
    parser.add_argument("--name", required=True, help="organization name")
    parser.add_argument("--email", required=True, help="owner email")
    parser.add_argument("--plan", default="team", help="free | team | enterprise")
    ns = parser.parse_args(rest)

    from app.tenancy.auth import get_store

    store = get_store()
    await store.setup()
    org = await store.create_org(ns.name, plan=ns.plan, owner_email=ns.email)
    plaintext, rec = await store.issue_api_key(org.id, name="owner-key", role="owner")
    console.print(
        Panel(
            f"[bold]Org:[/bold] {org.name}  ([dim]{org.id}[/dim])\n"
            f"[bold]Plan:[/bold] {org.plan}\n"
            f"[bold]Owner:[/bold] {ns.email}\n\n"
            f"[bold yellow]Owner API key (shown once):[/bold yellow]\n{plaintext}\n\n"
            "[dim]Set COPILOT_MULTI_TENANT=true and send this as 'Authorization: Bearer <key>'.[/dim]",
            title="✅ Tenant provisioned",
            border_style="green",
        )
    )


def main() -> None:
    observability.init()  # logging + LangSmith tracing (if enabled)
    argv = sys.argv[1:]
    if argv and argv[0] == "provision-org":
        asyncio.run(_provision_org(argv[1:]))
        return
    question = " ".join(argv) or None
    asyncio.run(_run(question))


if __name__ == "__main__":
    main()
