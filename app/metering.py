"""Usage metering + plan-quota enforcement (commercial; multi-tenant only).

Meters CONCLUSIVE investigations per tenant (errors/awaiting-approval don't count)
and gates new investigations against the tenant's monthly plan quota. Framework-
free (returns data / booleans; the API raises 402), and a no-op when no tenant is
on the contextvar — so the single-tenant offline path is unaffected. Stripe sync
(Phase 8) reads the same ledger; this is the local system of record.
"""

from __future__ import annotations

import logging

from app import tenant_context
from app.tenancy import auth as tenant_auth
from app.tenancy.models import quota, within_quota
from app.tenancy.store import month_start

log = logging.getLogger("devcopilot.metering")


async def record_investigation(tokens: int = 0) -> None:
    """Record one conclusive investigation (+ its token cost) for the current
    tenant. Best-effort: a metering failure never breaks the user's request."""
    cfg = tenant_context.get_tenant()
    if cfg is None:
        return
    try:
        store = tenant_auth.get_store()
        await store.record_usage(cfg.org_id, "investigation", 1, {"tokens": tokens})
        if tokens:
            await store.record_usage(cfg.org_id, "tokens", int(tokens))
    except Exception:  # noqa: BLE001
        log.exception("usage metering failed (org=%s)", cfg.org_id)


async def over_quota() -> bool:
    """True if the current tenant has hit its monthly investigation quota."""
    cfg = tenant_context.get_tenant()
    if cfg is None:
        return False
    used = await tenant_auth.get_store().usage_total(
        cfg.org_id, "investigation", since=month_start()
    )
    return not within_quota(cfg.plan, "investigations_per_month", used)


async def usage_summary() -> dict | None:
    """Current-period usage for the tenant (None in single-tenant mode)."""
    cfg = tenant_context.get_tenant()
    if cfg is None:
        return None
    store = tenant_auth.get_store()
    used = await store.usage_total(cfg.org_id, "investigation", since=month_start())
    tokens = await store.usage_total(cfg.org_id, "tokens", since=month_start())
    limit = quota(cfg.plan, "investigations_per_month")
    remaining = (limit - used) if limit >= 0 else -1
    return {
        "period": "month",
        "plan": cfg.plan,
        "investigations_used": used,
        "investigations_quota": limit,  # -1 = unlimited
        "investigations_remaining": remaining,
        "tokens_used": tokens,
        # soft upgrade nudge at 80% of a bounded quota
        "warning": bool(limit > 0 and used >= 0.8 * limit),
    }
