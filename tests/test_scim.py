"""SCIM 2.0 provisioning — payload parsing/serialization + store deprovision + guard."""

import asyncio

from app.tenancy import scim
from app.tenancy.store import TenantStore


def test_parse_scim_user_from_username():
    p = scim.parse_scim_user({"userName": "Alice@Example.com", "active": True})
    assert p == {"email": "alice@example.com", "active": True, "external_id": ""}


def test_parse_scim_user_falls_back_to_emails():
    p = scim.parse_scim_user({"emails": [{"value": "bob@x.io", "primary": True}]})
    assert p["email"] == "bob@x.io" and p["active"] is True


def test_to_scim_user_and_list():
    u = scim.to_scim_user("u1", "alice@x.io", active=True, role="responder")
    assert u["userName"] == "alice@x.io" and u["id"] == "u1"
    assert u["roles"][0]["value"] == "responder"
    lst = scim.scim_list([u])
    assert lst["totalResults"] == 1 and lst["Resources"] == [u]


def test_is_deprovision_variants():
    assert scim.is_deprovision({"active": False}) is True
    assert scim.is_deprovision({"Operations": [{"op": "replace", "value": {"active": False}}]}) is True
    assert scim.is_deprovision({"Operations": [{"op": "replace", "value": False}]}) is True
    assert scim.is_deprovision({"active": True}) is False


def test_store_provision_and_deprovision_roundtrip(tmp_path):
    s = TenantStore(str(tmp_path / "scim.sqlite"))

    async def scenario():
        await s.setup()
        org = await s.create_org("Acme")
        user = await s.create_user("alice@x.io")
        await s.add_member(org.id, user.id, "responder")
        assert await s.get_membership_by_email("alice@x.io") == (org.id, "responder")
        assert await s.remove_member(org.id, user.id) is True
        assert await s.get_membership_by_email("alice@x.io") is None
        assert await s.remove_member(org.id, user.id) is False  # already gone

    asyncio.run(scenario())


def test_scim_endpoints_403_when_unconfigured(monkeypatch):
    import app.api.main as api
    import app.config as cfg
    from fastapi.testclient import TestClient

    cfg.get_settings.cache_clear()
    client = TestClient(api.app)
    r = client.post("/scim/v2/Users", json={"userName": "x@y.io"},
                    headers={"Authorization": "Bearer whatever"})
    assert r.status_code == 403  # SCIM not configured
    cfg.get_settings.cache_clear()


def test_scim_endpoint_401_on_bad_token(monkeypatch):
    import app.api.main as api
    import app.config as cfg
    from fastapi.testclient import TestClient

    monkeypatch.setenv("COPILOT_SCIM_TOKEN", "secret-scim")
    monkeypatch.setenv("COPILOT_SCIM_ORG", "org-123")
    cfg.get_settings.cache_clear()
    client = TestClient(api.app)
    r = client.post("/scim/v2/Users", json={"userName": "x@y.io"},
                    headers={"Authorization": "Bearer wrong"})
    assert r.status_code == 401
    cfg.get_settings.cache_clear()
