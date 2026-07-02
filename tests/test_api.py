"""API surface tests via FastAPI TestClient — no LLM keys or MCP subprocesses
needed for any of these (they exercise probes, validation, auth, and the
production-hardening middleware, which all run before the agent is invoked)."""

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

import app.api.main as api
import app.config as cfg
from app import runtime


@pytest.fixture
def client():
    cfg.get_settings.cache_clear()
    api._RL.clear()
    runtime.reset()
    yield TestClient(api.app)
    cfg.get_settings.cache_clear()
    api._RL.clear()
    runtime.reset()


# --- probes / read-only endpoints ---------------------------------------- #
def test_healthz(client):
    r = client.get("/healthz")
    assert r.status_code == 200
    assert "active_sessions" in r.json()


def test_readyz_ready_in_dev(client):
    r = client.get("/readyz")
    assert r.status_code == 200
    assert r.json()["status"] == "ready"


def test_config(client):
    r = client.get("/config")
    assert r.status_code == 200
    body = r.json()
    for key in ("provider", "model", "fast_model", "servers", "github"):
        assert key in body


def test_metrics(client):
    r = client.get("/metrics")
    assert r.status_code == 200
    assert "services" in r.json()


def test_usage_single_tenant_reports_disabled(client):
    r = client.get("/usage")
    assert r.status_code == 200
    assert r.json()["multi_tenant"] is False


def test_audit_verify_endpoint(client):
    r = client.get("/audit/verify")
    assert r.status_code == 200
    assert "valid" in r.json()


def test_webhook_delivery_idempotency():
    api._SEEN_DELIVERIES.clear()
    assert api._claim_delivery(b"delivery-1") is True
    assert api._claim_delivery(b"delivery-1") is False  # redelivery deduped
    assert api._claim_delivery(b"delivery-2") is True


def test_request_id_header_on_every_response(client):
    assert client.get("/healthz").headers.get("x-request-id")


# --- validation ----------------------------------------------------------- #
def test_model_configure_bad_provider(client):
    assert client.post("/model/configure", json={"provider": "bogus"}).status_code == 400


def test_github_connect_bad_body(client):
    assert client.post("/github/connect", json={"token": "", "repo": "noslash"}).status_code == 400


def test_normalize_repo_accepts_urls_and_owner_repo():
    assert api._normalize_repo("https://github.com/acme/app") == "acme/app"
    assert api._normalize_repo("https://github.com/acme/app.git") == "acme/app"
    assert api._normalize_repo("https://github.com/acme/app/tree/main") == "acme/app"
    assert api._normalize_repo("git@github.com:acme/app.git") == "acme/app"
    assert api._normalize_repo("acme/app") == "acme/app"
    assert api._normalize_repo("  acme/app/  ") == "acme/app"


def test_signup_requires_multi_tenant(client):
    # Self-serve signup is a no-op in single-tenant mode (the offline demo default).
    assert client.post("/signup", json={"org_name": "X", "email": "a@b.com"}).status_code == 400


def test_empty_message_rejected(client):
    assert client.post("/chat", json={"thread_id": "t", "message": "   "}).status_code == 400


def test_message_too_long(client, monkeypatch):
    monkeypatch.setenv("COPILOT_MAX_MESSAGE_CHARS", "10")
    cfg.get_settings.cache_clear()
    r = client.post("/chat", json={"thread_id": "t", "message": "x" * 50})
    assert r.status_code == 413


def test_body_too_large(client, monkeypatch):
    monkeypatch.setenv("COPILOT_MAX_BODY_BYTES", "20")
    cfg.get_settings.cache_clear()
    r = client.post("/chat", json={"thread_id": "t", "message": "a reasonably long body here"})
    assert r.status_code == 413


# --- middleware: rate limiting ------------------------------------------- #
def test_rate_limit_returns_429(client, monkeypatch):
    monkeypatch.setenv("COPILOT_RATE_LIMIT_PER_MIN", "2")
    cfg.get_settings.cache_clear()
    api._RL.clear()
    codes = [client.post("/model/configure", json={"provider": "bogus"}).status_code for _ in range(4)]
    assert 429 in codes  # counted before routing, so even 400s trip the limiter


# --- auth ----------------------------------------------------------------- #
def test_auth_required_when_token_set(client, monkeypatch):
    monkeypatch.setenv("COPILOT_API_TOKEN", "secret")
    cfg.get_settings.cache_clear()
    assert client.get("/config").status_code == 401
    assert client.get("/config", headers={"Authorization": "Bearer secret"}).status_code == 200
    assert client.get("/config", headers={"Authorization": "Bearer wrong"}).status_code == 401
    assert client.get("/healthz").status_code == 200  # probe is exempt


# --- source-path lockdown (unit) ----------------------------------------- #
def test_source_lockdown_rejects_outside_root():
    with pytest.raises(HTTPException) as ei:
        api._set_source("/etc", "repo", lambda p: None)
    assert ei.value.status_code in (403, 404)


# --- friendly error coercion --------------------------------------------- #
def test_friendly_error_branches():
    assert "rate limit" in api._friendly_error(Exception("429 rate_limit")).lower()
    assert "key" in api._friendly_error(Exception("401 authentication error")).lower()
    assert "went wrong" in api._friendly_error(Exception("boom")).lower()


# --- autonomous remediation (#4) ------------------------------------------ #
def test_remediate_forbidden_when_autonomy_disabled(client):
    r = client.post("/remediate", json={
        "action": "rollback_deployment", "target": "checkout-svc", "confidence": "high"})
    assert r.status_code == 403


def test_remediate_rejects_non_reversible_action(client, monkeypatch):
    monkeypatch.setenv("COPILOT_AUTONOMY", "true")
    cfg.get_settings.cache_clear()
    r = client.post("/remediate", json={
        "action": "scale_deployment", "target": "checkout-svc", "confidence": "high"})
    assert r.status_code == 400
    assert "reversible" in r.json()["detail"]


def test_remediate_dry_run_when_enabled(client, monkeypatch):
    monkeypatch.setenv("COPILOT_AUTONOMY", "true")  # dry-run defaults on
    cfg.get_settings.cache_clear()
    r = client.post("/remediate", json={
        "action": "restart_deployment", "target": "checkout-svc", "confidence": "high"})
    assert r.status_code == 200
    assert r.json()["status"] == "dry_run"


def test_remediate_requires_high_confidence(client, monkeypatch):
    monkeypatch.setenv("COPILOT_AUTONOMY", "true")
    cfg.get_settings.cache_clear()
    r = client.post("/remediate", json={
        "action": "restart_deployment", "target": "checkout-svc", "confidence": "low"})
    assert r.status_code == 400


def test_global_spend_cap_blocks_new_investigation(client, monkeypatch):
    monkeypatch.setenv("COPILOT_GLOBAL_TOKEN_CAP", "1000")
    cfg.get_settings.cache_clear()

    class _Full:
        async def total(self):
            return 5000  # already over the cap

        async def record(self, tokens):
            return 5000

    monkeypatch.setattr(api, "_SPEND", _Full())
    r = client.post("/chat", json={"thread_id": "t", "message": "why 500s?"})
    assert r.status_code == 429
    assert "budget" in r.json()["detail"].lower()
    monkeypatch.setattr(api, "_SPEND", None)


def test_me_single_tenant_returns_operator(client):
    r = client.get("/me")
    assert r.status_code == 200
    body = r.json()
    assert body["multi_tenant"] is False
    assert body["authenticated"] is True
    assert body["label"] == "Operator"
