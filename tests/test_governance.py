"""Data governance — retention purge, GDPR erasure, audit export."""

import asyncio

import pytest

from app import audit, governance
from app.tenancy.store import TenantStore


def _store(tmp_path):
    s = TenantStore(str(tmp_path / "g.sqlite"))
    asyncio.run(s.setup())
    return s


def test_retention_cutoff_is_deterministic():
    # 2026-07-03T00:00:00Z epoch = 1783036800; 7 days earlier = 2026-06-26.
    assert governance.retention_cutoff(7, now_epoch=1783036800.0) == "2026-06-26T00:00:00Z"
    assert governance.retention_cutoff(0, now_epoch=1783036800.0) == "2026-07-03T00:00:00Z"


def test_apply_retention_purges_old_usage(tmp_path):
    s = _store(tmp_path)

    async def scenario():
        org = await s.create_org("Acme")
        import aiosqlite
        async with aiosqlite.connect(s.db_path) as db:
            await db.execute("INSERT INTO usage (org_id, kind, amount, ts) VALUES (?,?,?,?)",
                             (org.id, "tokens", 100, "2020-01-01T00:00:00Z"))  # ancient
            await db.execute("INSERT INTO usage (org_id, kind, amount, ts) VALUES (?,?,?,?)",
                             (org.id, "tokens", 100, "2999-01-01T00:00:00Z"))  # fresh
            await db.commit()
        removed = await governance.apply_retention(s, days=30, now_epoch=1783036800.0)
        assert removed == 1  # only the ancient row
        assert await governance.apply_retention(s, days=0) == 0  # disabled

    asyncio.run(scenario())


def test_gdpr_delete_org_erases_everything(tmp_path):
    pytest.importorskip("cryptography")
    s = _store(tmp_path)

    async def scenario():
        org = await s.create_org("Acme")
        await s.set_integration_secret(org.id, "DD_API_KEY", "secret")
        assert await governance.gdpr_delete_org(s, org.id) is True
        assert await s.get_org(org.id) is None                 # org gone
        assert await s.get_integration_secret(org.id, "DD_API_KEY") is None
        assert await governance.gdpr_delete_org(s, "nope") is False

    asyncio.run(scenario())


def test_export_audit_writes_jsonl_and_reports_chain(tmp_path):
    audit.clear()
    audit.record("approval.decided", approved=True)
    audit.record("config.model_changed", provider="anthropic")
    events = audit.recent(limit=10)[::-1]  # oldest→newest for chain verify
    out = governance.export_audit(events, str(tmp_path / "audit_export.jsonl"))
    assert out["exported"] == 2
    assert out["chain_ok"] is True
    lines = (tmp_path / "audit_export.jsonl").read_text().strip().splitlines()
    assert len(lines) == 2
