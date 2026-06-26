"""Tenant-context isolation — the build-blocking guarantees every later phase
relies on. Deny-by-default: no context ⇒ no tenant; a set context never leaks
across resets or into the wrong task; audit lines are tenant-stamped."""

import asyncio

from app import audit
from app.tenant_context import (
    TenantConfig,
    get_tenant,
    reset_tenant,
    run_with_tenant,
    set_actor,
    set_tenant,
    tenant_id,
)


def test_deny_by_default_no_context_means_no_tenant():
    assert get_tenant() is None
    assert tenant_id() == "-"


def test_set_get_reset_roundtrip():
    token = set_tenant(TenantConfig(org_id="acme", plan="team", role="admin"))
    try:
        cfg = get_tenant()
        assert cfg and cfg.org_id == "acme" and cfg.role == "admin"
        assert tenant_id() == "acme"
    finally:
        reset_tenant(token)
    assert get_tenant() is None  # reset restores deny-by-default


def test_tenant_a_never_yields_tenant_b():
    ta = set_tenant(TenantConfig(org_id="A"))
    assert get_tenant().org_id == "A"
    reset_tenant(ta)
    tb = set_tenant(TenantConfig(org_id="B"))
    try:
        assert get_tenant().org_id == "B"  # no bleed from A
    finally:
        reset_tenant(tb)


def test_run_with_tenant_in_background_task_reestablishes_context():
    async def scenario():
        cfg_a = TenantConfig(org_id="A")

        async def task_body():
            # The task was created with NO tenant in context; run_with_tenant
            # re-establishes it so background work is correctly scoped.
            return get_tenant().org_id if get_tenant() else None

        result = await asyncio.create_task(run_with_tenant(cfg_a, task_body()))
        assert result == "A"
        assert get_tenant() is None  # parent context untouched

    asyncio.run(scenario())


def test_audit_lines_are_tenant_and_actor_stamped():
    audit.clear()
    audit.record("test.event_no_tenant")
    token = set_tenant(TenantConfig(org_id="acme"))
    set_actor("key:ci")
    try:
        audit.record("test.event_with_tenant")
    finally:
        reset_tenant(token)
        set_actor("-")

    events = {e["event"]: e for e in audit.recent()}
    assert events["test.event_no_tenant"]["org_id"] == "-"
    assert events["test.event_with_tenant"]["org_id"] == "acme"
    assert events["test.event_with_tenant"]["actor"] == "key:ci"
