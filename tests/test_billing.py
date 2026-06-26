"""Stripe usage-sync adapter over the local ledger (offline, FakeStripeClient)."""

import asyncio

from app import billing
from app.tenancy.store import TenantStore


def _run(coro):
    return asyncio.run(coro)


def _store(tmp_path):
    s = TenantStore(str(tmp_path / "t.sqlite"))
    _run(s.setup())
    return s


def test_sync_pushes_unsynced_then_is_idempotent(tmp_path):
    s = _store(tmp_path)

    async def scenario():
        org = await s.create_org("Acme")
        await s.set_stripe_customer(org.id, "cus_123")
        await s.record_usage(org.id, "investigation", 1, event_key="e1")
        await s.record_usage(org.id, "investigation", 1, event_key="e2")

        client = billing.FakeStripeClient()
        n = await billing.sync_usage_to_stripe(s, client)
        assert n == 2
        assert len(client.events) == 2
        assert {e["idempotency_key"] for e in client.events} == {"e1", "e2"}
        assert client.events[0]["customer_id"] == "cus_123"

        # second run: everything is already synced -> nothing new sent
        n2 = await billing.sync_usage_to_stripe(s, client)
        assert n2 == 0
        assert len(client.events) == 2

    _run(scenario())


def test_sync_skips_orgs_without_a_stripe_customer(tmp_path):
    s = _store(tmp_path)

    async def scenario():
        org = await s.create_org("NoBilling")  # no stripe customer mapped
        await s.record_usage(org.id, "investigation", 1, event_key="x1")
        client = billing.FakeStripeClient()
        assert await billing.sync_usage_to_stripe(s, client) == 0
        assert client.events == []

    _run(scenario())


def test_get_client_none_without_key(monkeypatch):
    import app.config as cfg

    monkeypatch.delenv("STRIPE_API_KEY", raising=False)
    cfg.get_settings.cache_clear()
    assert billing.get_client() is None
    cfg.get_settings.cache_clear()
