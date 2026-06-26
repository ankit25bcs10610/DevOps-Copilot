"""Multi-tenant API behavior: tenant-scoped API-key auth + RBAC enforcement.

These run the real FastAPI app with COPILOT_MULTI_TENANT=true against a tmp tenant
store. They exercise auth + RBAC, which run before the agent is invoked, so no LLM
key / MCP subprocess is needed.
"""

import asyncio

import pytest
from fastapi.testclient import TestClient

import app.api.main as api
import app.config as cfg
from app import runtime, tenant_context
from app.tenancy import auth as tenant_auth


async def _provision(store):
    await store.setup()
    org_a = await store.create_org("Acme", plan="team")
    admin_key, _ = await store.issue_api_key(org_a.id, "k-admin", role="admin")
    viewer_key, _ = await store.issue_api_key(org_a.id, "k-view", role="viewer")
    org_b = await store.create_org("Beta", plan="free")
    b_key, _ = await store.issue_api_key(org_b.id, "k-b", role="responder")
    return {"admin": admin_key, "viewer": viewer_key, "b": b_key,
            "org_a": org_a.id, "org_b": org_b.id}


@pytest.fixture
def mt(tmp_path, monkeypatch):
    monkeypatch.setenv("COPILOT_MULTI_TENANT", "true")
    monkeypatch.setenv("COPILOT_TENANT_DB", str(tmp_path / "tenants.sqlite"))
    cfg.get_settings.cache_clear()
    tenant_auth.reset_store_cache()
    tenant_context.current_tenant.set(None)
    api._RL.clear()
    runtime.reset()
    data = asyncio.run(_provision(tenant_auth.get_store()))
    data["client"] = TestClient(api.app)
    yield data
    cfg.get_settings.cache_clear()
    tenant_auth.reset_store_cache()
    tenant_context.current_tenant.set(None)
    api._RL.clear()
    runtime.reset()


def _auth(key: str) -> dict:
    return {"Authorization": f"Bearer {key}"}


def test_no_key_is_unauthorized(mt):
    assert mt["client"].get("/config").status_code == 401


def test_invalid_key_is_unauthorized(mt):
    assert mt["client"].get("/config", headers=_auth("dcp_bad_key")).status_code == 401
    assert mt["client"].get("/config", headers=_auth("garbage")).status_code == 401


def test_valid_key_authorizes(mt):
    r = mt["client"].get("/config", headers=_auth(mt["admin"]))
    assert r.status_code == 200
    assert "provider" in r.json()


def test_rbac_viewer_denied_manage_integrations(mt):
    # viewer cannot configure the model (manage_integrations => admin+)
    r = mt["client"].post("/model/configure", json={"provider": "anthropic", "api_key": "x"},
                          headers=_auth(mt["viewer"]))
    assert r.status_code == 403


def test_rbac_admin_passes_rbac_then_hits_validation(mt):
    # admin passes the RBAC gate, so a bad provider reaches handler validation (400, not 403)
    r = mt["client"].post("/model/configure", json={"provider": "bogus"},
                          headers=_auth(mt["admin"]))
    assert r.status_code == 400


def test_revoked_key_is_unauthorized(mt):
    # revoke the viewer key, then it must stop working
    async def revoke():
        store = tenant_auth.get_store()
        resolved = await store.resolve_api_key(mt["viewer"])
        assert resolved is not None
        _, key = resolved
        await store.revoke_api_key(key.id)

    asyncio.run(revoke())
    assert mt["client"].get("/config", headers=_auth(mt["viewer"])).status_code == 401
