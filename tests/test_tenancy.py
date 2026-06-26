"""Multi-tenant foundation: RBAC + plans/quotas (pure) and the async tenant store."""

import asyncio

import pytest

from app.tenancy import models
from app.tenancy.store import TenantStore, month_start


# --- models (pure policy) -------------------------------------------------- #
def test_rbac_permission_matrix():
    assert models.can("owner", "delete_org")
    assert not models.can("admin", "delete_org")
    assert models.can("admin", "manage_integrations")
    assert not models.can("responder", "manage_integrations")
    assert models.can("responder", "approve_action")
    assert not models.can("viewer", "run_investigation")
    assert models.can("viewer", "view")
    assert not models.can("viewer", "unknown_action")


def test_quota_helpers():
    assert models.quota("free", "investigations_per_month") == 50
    assert models.quota("enterprise", "investigations_per_month") == -1
    assert models.within_quota("free", "seats", 2)
    assert not models.within_quota("free", "seats", 3)
    assert models.within_quota("enterprise", "seats", 10_000)  # unlimited


def test_normalize_defaults():
    assert models.normalize_role("ADMIN") == "admin"
    assert models.normalize_role("bogus") == "viewer"
    assert models.normalize_plan("Team") == "team"
    assert models.normalize_plan("bogus") == "free"


# --- store (async, driven via asyncio.run) -------------------------------- #
def _run(coro):
    return asyncio.run(coro)


def _store(tmp_path):
    s = TenantStore(str(tmp_path / "t.sqlite"))
    _run(s.setup())
    return s


def test_org_user_member_roundtrip(tmp_path):
    s = _store(tmp_path)

    async def scenario():
        org = await s.create_org("Acme", plan="team", owner_email="o@acme.com")
        got = await s.get_org(org.id)
        assert got and got.name == "Acme" and got.plan == "team"
        assert await s.get_user_by_email("o@acme.com") is not None
        assert await s.count_members(org.id) == 1  # owner auto-added

    _run(scenario())


def test_api_key_issue_resolve_revoke(tmp_path):
    s = _store(tmp_path)

    async def scenario():
        org = await s.create_org("Acme")
        plaintext, rec = await s.issue_api_key(org.id, name="ci", role="responder")
        assert plaintext.startswith("dcp_")
        resolved = await s.resolve_api_key(plaintext)
        assert resolved is not None
        ro, rk = resolved
        assert ro.id == org.id and rk.role == "responder"
        # malformed / unknown keys are rejected
        assert await s.resolve_api_key("dcp_deadbeef_nope") is None
        assert await s.resolve_api_key("garbage") is None
        # revocation
        await s.revoke_api_key(rec.id)
        assert await s.resolve_api_key(plaintext) is None

    _run(scenario())


def test_integration_secret_roundtrip(tmp_path):
    pytest.importorskip("cryptography")
    s = _store(tmp_path)

    async def scenario():
        org = await s.create_org("Acme")
        await s.set_integration_secret(org.id, "DD_API_KEY", "supersecret")
        assert await s.get_integration_secret(org.id, "DD_API_KEY") == "supersecret"
        alls = await s.get_integration_secrets(org.id)
        assert alls["DD_API_KEY"] == "supersecret"
        assert await s.count_integrations(org.id) == 1

    _run(scenario())


def test_usage_metering_and_quota(tmp_path):
    s = _store(tmp_path)

    async def scenario():
        org = await s.create_org("Acme", plan="free")
        for _ in range(3):
            await s.record_usage(org.id, "investigation", 1)
        total = await s.usage_total(org.id, "investigation", since=month_start())
        assert total == 3
        assert models.within_quota("free", "investigations_per_month", total)

    _run(scenario())


def test_postgres_url_rejected_clearly():
    with pytest.raises(RuntimeError):
        TenantStore("postgresql://example/db")
