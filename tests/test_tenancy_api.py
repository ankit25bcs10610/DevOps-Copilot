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
    owner_key, _ = await store.issue_api_key(org_a.id, "k-owner", role="owner")
    admin_key, _ = await store.issue_api_key(org_a.id, "k-admin", role="admin")
    viewer_key, _ = await store.issue_api_key(org_a.id, "k-view", role="viewer")
    org_b = await store.create_org("Beta", plan="free")
    b_key, b_rec = await store.issue_api_key(org_b.id, "k-b", role="responder")
    return {"owner": owner_key, "admin": admin_key, "viewer": viewer_key, "b": b_key,
            "b_key_id": b_rec.id, "org_a": org_a.id, "org_b": org_b.id}


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


def test_usage_endpoint_reports_plan_and_quota(mt):
    r = mt["client"].get("/usage", headers=_auth(mt["admin"]))
    assert r.status_code == 200
    body = r.json()
    assert body["plan"] == "team"
    assert body["investigations_quota"] == 1000
    assert body["investigations_used"] == 0


def test_over_quota_blocks_new_investigation_with_402(mt):
    # org_b is on the free plan (50/month). Seed it to the limit, then /chat is gated.
    async def seed():
        store = tenant_auth.get_store()
        for _ in range(50):
            await store.record_usage(mt["org_b"], "investigation", 1)

    asyncio.run(seed())
    r = mt["client"].post("/chat", json={"thread_id": "t1", "message": "why 500s?"},
                          headers=_auth(mt["b"]))
    assert r.status_code == 402


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


# --- admin / tenant-management endpoints (Phase 5) ------------------------ #
def test_admin_create_key_and_use_it(mt):
    c = mt["client"]
    r = c.post("/admin/api-keys", json={"name": "ci", "role": "responder"},
               headers=_auth(mt["admin"]))
    assert r.status_code == 200
    new_key = r.json()["api_key"]
    assert new_key.startswith("dcp_")
    # the freshly-minted key authenticates
    assert c.get("/config", headers=_auth(new_key)).status_code == 200


def test_admin_revoke_is_org_scoped(mt):
    c = mt["client"]
    # admin (org A) cannot revoke org B's key id -> 404
    assert c.request("DELETE", f"/admin/api-keys/{mt['b_key_id']}",
                     headers=_auth(mt["admin"])).status_code == 404


def test_viewer_cannot_manage_keys(mt):
    assert mt["client"].post("/admin/api-keys", json={}, headers=_auth(mt["viewer"])).status_code == 403


def test_admin_set_and_list_integration(mt):
    pytest.importorskip("cryptography")
    c = mt["client"]
    assert c.post("/admin/integrations", json={"name": "DD_API_KEY", "value": "secret"},
                  headers=_auth(mt["admin"])).status_code == 200
    body = c.get("/admin/integrations", headers=_auth(mt["admin"])).json()
    assert "DD_API_KEY" in body["integrations"]  # names only, never values


def test_admin_org_summary(mt):
    r = mt["client"].get("/admin/org", headers=_auth(mt["admin"]))
    assert r.status_code == 200
    assert r.json()["plan"] == "team"


def test_supabase_jwt_login_maps_member_to_org(mt, monkeypatch):
    from app.tenancy import supabase_auth

    async def add_member():
        store = tenant_auth.get_store()
        u = await store.create_user("dev@acme.com")
        await store.add_member(mt["org_a"], u.id, "responder")

    asyncio.run(add_member())
    # stub JWT verification (real verification is covered in test_supabase_auth.py)
    monkeypatch.setattr(supabase_auth, "verify_jwt",
                        lambda t: {"email": "dev@acme.com", "aud": "authenticated"})
    # a 2-dot token routes to the JWT path; the member is authorized
    r = mt["client"].get("/config", headers={"Authorization": "Bearer aaa.bbb.ccc"})
    assert r.status_code == 200

    # an authenticated but non-member identity is rejected
    monkeypatch.setattr(supabase_auth, "verify_jwt", lambda t: {"email": "stranger@nope.com"})
    assert mt["client"].get("/config", headers={"Authorization": "Bearer aaa.bbb.ccc"}).status_code == 401


def test_signup_creates_org_and_working_key(mt):
    c = mt["client"]
    r = c.post("/signup", json={"org_name": "NewCo", "email": "Founder@NewCo.com"})
    assert r.status_code == 200
    body = r.json()
    assert body["api_key"].startswith("dcp_")
    assert body["plan"] == "free" and body["role"] == "owner"
    # the freshly-issued owner key authenticates immediately
    assert c.get("/config", headers=_auth(body["api_key"])).status_code == 200


def test_signup_is_capped_to_one_org_per_email(mt):
    c = mt["client"]
    assert c.post("/signup", json={"org_name": "First", "email": "dup@x.com"}).status_code == 200
    # a second signup with the same email is refused (free-tier farming / squatting guard)
    assert c.post("/signup", json={"org_name": "Second", "email": "dup@x.com"}).status_code == 409


def test_signup_rejects_bad_input(mt):
    c = mt["client"]
    assert c.post("/signup", json={"org_name": "X", "email": "not-an-email"}).status_code == 400
    assert c.post("/signup", json={"org_name": "", "email": "a@b.com"}).status_code == 400


def test_signup_disabled_returns_403(mt, monkeypatch):
    monkeypatch.setenv("COPILOT_SIGNUP_ENABLED", "false")
    cfg.get_settings.cache_clear()
    assert mt["client"].post("/signup", json={"org_name": "X", "email": "a@b.com"}).status_code == 403


def test_billing_requires_owner(mt):
    c = mt["client"]
    # admin cannot change plan (manage_billing => owner)
    assert c.patch("/admin/plan", json={"plan": "enterprise"}, headers=_auth(mt["admin"])).status_code == 403
    # owner can
    r = c.patch("/admin/plan", json={"plan": "enterprise"}, headers=_auth(mt["owner"]))
    assert r.status_code == 200 and r.json()["plan"] == "enterprise"
