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


def test_request_id_header_on_every_response(client):
    assert client.get("/healthz").headers.get("x-request-id")


# --- validation ----------------------------------------------------------- #
def test_model_configure_bad_provider(client):
    assert client.post("/model/configure", json={"provider": "bogus"}).status_code == 400


def test_github_connect_bad_body(client):
    assert client.post("/github/connect", json={"token": "", "repo": "noslash"}).status_code == 400


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
