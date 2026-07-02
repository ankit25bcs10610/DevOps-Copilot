"""Stripe webhook — signature verification + subscription-event → plan mapping."""

import hashlib
import hmac

from app.billing import plan_from_stripe_event, verify_stripe_signature


def _sig(payload: bytes, secret: str, ts: int) -> str:
    signed = f"{ts}.".encode() + payload
    v1 = hmac.new(secret.encode(), signed, hashlib.sha256).hexdigest()
    return f"t={ts},v1={v1}"


def test_verify_signature_accepts_valid():
    body = b'{"type":"x"}'
    header = _sig(body, "whsec_test", 1000)
    assert verify_stripe_signature(body, header, "whsec_test", now=1000) is True


def test_verify_signature_rejects_wrong_secret_and_missing():
    body = b'{"a":1}'
    header = _sig(body, "right", 1000)
    assert verify_stripe_signature(body, header, "wrong", now=1000) is False
    assert verify_stripe_signature(body, header, "", now=1000) is False   # fail closed
    assert verify_stripe_signature(body, "", "right", now=1000) is False


def test_verify_signature_rejects_stale_timestamp():
    body = b'{"a":1}'
    header = _sig(body, "s", 1000)
    assert verify_stripe_signature(body, header, "s", now=1000 + 10_000) is False  # outside tolerance


def test_plan_mapping_created_updated_deleted():
    def evt(t, meta):
        return {"type": t, "data": {"object": {"metadata": meta}}}

    assert plan_from_stripe_event(evt("customer.subscription.updated",
                                     {"org_id": "o1", "plan": "team"})) == ("o1", "team")
    assert plan_from_stripe_event(evt("customer.subscription.created",
                                     {"org_id": "o1", "plan": "enterprise"})) == ("o1", "enterprise")
    assert plan_from_stripe_event(evt("customer.subscription.deleted",
                                     {"org_id": "o1"})) == ("o1", "free")


def test_plan_mapping_ignores_unknown_or_incomplete():
    def evt(t, meta):
        return {"type": t, "data": {"object": {"metadata": meta}}}

    assert plan_from_stripe_event(evt("customer.subscription.updated", {"plan": "team"})) is None  # no org
    assert plan_from_stripe_event(evt("customer.subscription.updated",
                                      {"org_id": "o1", "plan": "bogus"})) is None  # bad plan
    assert plan_from_stripe_event(evt("invoice.paid", {"org_id": "o1"})) is None   # unhandled type


def test_endpoint_rejects_bad_signature(monkeypatch):
    import app.api.main as api
    import app.config as cfg
    from fastapi.testclient import TestClient

    monkeypatch.setenv("STRIPE_WEBHOOK_SECRET", "whsec_test")
    cfg.get_settings.cache_clear()
    client = TestClient(api.app)
    r = client.post("/webhooks/stripe", content=b'{"type":"x"}',
                    headers={"stripe-signature": "t=1,v1=deadbeef"})
    assert r.status_code == 401
    cfg.get_settings.cache_clear()
