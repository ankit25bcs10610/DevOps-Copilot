"""Progressive-autonomy remediation — eligibility gate, recovery eval, and the
apply→watch→auto-revert controller (all execution injected)."""

import asyncio

from app.autonomy import (
    AutonomyController,
    RemediationPlan,
    evaluate_recovery,
    is_eligible,
)

_PAGE = {"verdict": "page (fast burn)", "burn_rate_short": 20.0}
_PAGE_WORSE = {"verdict": "page (fast burn)", "burn_rate_short": 30.0}
_OK = {"verdict": "ok (within error budget)", "burn_rate_short": 0.5}


async def _noop_sleep(_s):
    return None


# --- eligibility gate ------------------------------------------------------ #
def test_is_eligible_requires_enabled_reversible_and_high_confidence():
    assert is_eligible("rollback_deployment", "high", True)[0] is True
    assert is_eligible("rollback_deployment", "high", False)[0] is False   # disabled
    assert is_eligible("scale_deployment", "high", True)[0] is False       # not reversible
    assert is_eligible("rollback_deployment", "medium", True)[0] is False  # confidence too low


def test_is_eligible_gives_a_reason_when_blocked():
    ok, why = is_eligible("scale_deployment", "high", True)
    assert not ok and "reversible" in why


# --- recovery evaluation --------------------------------------------------- #
def test_evaluate_recovery():
    assert evaluate_recovery(_PAGE, _OK) == "recovered"
    assert evaluate_recovery(_PAGE, _PAGE_WORSE) == "regressed"
    assert evaluate_recovery(_PAGE, _PAGE) == "no_change"


# --- controller ------------------------------------------------------------ #
def _controller(observe_seq, executed, reverted, dry_run=False):
    calls = iter(observe_seq)

    async def execute(action, target):
        executed.append((action, target))
        return {"status": "applied"}

    async def revert(action, target):
        reverted.append((action, target))
        return {"status": "reverted"}

    return AutonomyController(
        observe_fn=lambda _t: next(calls),
        execute_fn=execute,
        revert_fn=revert,
        dry_run=dry_run,
        watch_s=0,
        sleep=_noop_sleep,
    )


def test_dry_run_does_not_execute():
    executed: list = []
    ctrl = _controller([_PAGE], executed, [], dry_run=True)
    out = asyncio.run(ctrl.remediate(RemediationPlan("restart_deployment", "checkout")))
    assert out["status"] == "dry_run"
    assert executed == []


def test_success_when_signal_recovers():
    executed: list = []
    reverted: list = []
    ctrl = _controller([_PAGE, _OK], executed, reverted)  # before=page, after=ok
    out = asyncio.run(ctrl.remediate(RemediationPlan("restart_deployment", "checkout")))
    assert out["status"] == "succeeded"
    assert executed == [("restart_deployment", "checkout")]
    assert reverted == []


def test_auto_reverts_when_not_recovered():
    executed: list = []
    reverted: list = []
    ctrl = _controller([_PAGE, _PAGE_WORSE], executed, reverted)  # still bad after
    plan = RemediationPlan("rollback_deployment", "checkout", revert_action="restart_deployment")
    out = asyncio.run(ctrl.remediate(plan))
    assert out["status"] == "rolled_back"
    assert reverted == [("restart_deployment", "checkout")]


def test_escalates_when_not_recovered_and_no_revert():
    executed: list = []
    reverted: list = []
    ctrl = _controller([_PAGE, _PAGE], executed, reverted)  # no_change, no revert action
    out = asyncio.run(ctrl.remediate(RemediationPlan("restart_deployment", "checkout")))
    assert out["status"] == "escalated"
    assert reverted == []
