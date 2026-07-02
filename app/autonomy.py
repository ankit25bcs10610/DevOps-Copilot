"""Progressive-autonomy remediation — apply a fix, watch, auto-revert on regression.

The closed loop beyond "propose a PR": for a REVERSIBLE, well-evidenced remediation,
apply it, watch the incident's signal for a window, and — if it doesn't recover —
execute the compensating action and escalate to a human.

Safe by construction:
  - OFF by default (`COPILOT_AUTONOMY`) and DRY-RUN by default (`COPILOT_AUTONOMY_DRYRUN`):
    real infra is mutated only when an operator opts into both.
  - Reversible actions ONLY (rollback / restart) — never scale-to-zero or a PR merge.
  - Gated on HIGH investigation confidence (reuses the policy confidence tiers).
  - Every step is caller-injected (execute / observe / revert), so the decision
    logic is pure and unit-testable and the blast radius lives at the wiring edge.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from typing import Awaitable, Callable

from app.slo_poller import is_page_worthy

log = logging.getLogger("devcopilot.autonomy")

# The only actions autonomy may take unattended — reversible, bounded blast radius.
REVERSIBLE_ACTIONS: set[str] = {"rollback_deployment", "restart_deployment"}


@dataclass
class RemediationPlan:
    action: str
    target: str
    reason: str = ""
    # The compensating action to undo `action` if the signal regresses (optional;
    # a restart has nothing to undo, a rollback can be rolled forward).
    revert_action: str | None = None
    namespace: str = ""


def is_eligible(action: str, confidence: str, enabled: bool) -> tuple[bool, str]:
    """Pure gate: may autonomy take `action` at this investigation `confidence`?
    Returns (ok, reason-when-not)."""
    if not enabled:
        return False, "autonomy is disabled (COPILOT_AUTONOMY=false)"
    if action not in REVERSIBLE_ACTIONS:
        return False, f"'{action}' is not a reversible auto-remediation ({sorted(REVERSIBLE_ACTIONS)})"
    if confidence != "high":
        return False, f"investigation confidence is '{confidence}', not high enough to act autonomously"
    return True, ""


def _short_burn(sig: dict) -> float:
    try:
        return float(sig.get("burn_rate_short", sig.get("current_error_rate", 0)) or 0)
    except (TypeError, ValueError):
        return 0.0


def evaluate_recovery(before: dict, after: dict) -> str:
    """Did the remediation help? 'recovered' when the signal is no longer page-worthy;
    otherwise 'regressed' if it got materially worse, else 'no_change'. Pure."""
    if not is_page_worthy(after):
        return "recovered"
    return "regressed" if _short_burn(after) > _short_burn(before) * 1.1 else "no_change"


@dataclass
class AutonomyController:
    """Runs the apply→watch→auto-revert loop. Execution is injected so this is
    testable and the real infra calls live at the wiring edge."""

    observe_fn: Callable[[str], dict]                    # target -> signal (burn dict)
    execute_fn: Callable[[str, str], Awaitable[dict]]    # (action, target) -> result
    revert_fn: Callable[[str, str], Awaitable[dict]]     # (revert_action, target) -> result
    dry_run: bool = True
    watch_s: float = 60.0
    sleep: Callable[[float], Awaitable[None]] = field(default=asyncio.sleep)

    async def remediate(self, plan: RemediationPlan) -> dict:
        """Execute the plan (or simulate it under dry-run) and report the outcome."""
        before = self.observe_fn(plan.target)
        base: dict = {
            "action": plan.action, "target": plan.target, "reason": plan.reason,
            "before": before,
        }
        if self.dry_run:
            log.info("autonomy DRY-RUN: would %s %s", plan.action, plan.target)
            return {**base, "status": "dry_run",
                    "detail": "dry-run — no infra changed (set COPILOT_AUTONOMY_DRYRUN=false to act)"}

        log.info("autonomy: executing %s on %s", plan.action, plan.target)
        applied = await self.execute_fn(plan.action, plan.target)
        await self.sleep(self.watch_s)
        after = self.observe_fn(plan.target)
        outcome = evaluate_recovery(before, after)
        result = {**base, "applied": applied, "after": after, "outcome": outcome}

        if outcome == "recovered":
            return {**result, "status": "succeeded",
                    "detail": "signal recovered after the remediation"}
        # Did not recover — undo if we can, and always escalate to a human.
        if plan.revert_action:
            log.warning("autonomy: %s did not recover %s; reverting via %s",
                        plan.action, plan.target, plan.revert_action)
            reverted = await self.revert_fn(plan.revert_action, plan.target)
            return {**result, "status": "rolled_back", "reverted": reverted,
                    "detail": "remediation did not recover the signal — reverted; escalating to a human"}
        return {**result, "status": "escalated",
                "detail": "remediation did not recover the signal and has no revert — escalating to a human"}
