"""Trigger webhooks: PagerDuty + Slack signature verification, parsing, and the
endpoint gates. No live calls — signatures are computed deterministically and no
LLM key is present, so the webhook accepts without spawning an investigation."""

import hashlib
import hmac
import json
import time

import pytest
from fastapi.testclient import TestClient

import app.api.main as api
import app.config as cfg
from app import runtime
from app.integrations import pagerduty as pd
from app.integrations import slack

_PROVIDER_KEYS = (
    "ANTHROPIC_API_KEY", "OPENAI_API_KEY", "GEMINI_API_KEY", "GROQ_API_KEY", "DEEPSEEK_API_KEY",
)


@pytest.fixture
def client(monkeypatch):
    for k in _PROVIDER_KEYS:  # ensure no LLM key -> webhook never spawns a real run
        monkeypatch.delenv(k, raising=False)
    cfg.get_settings.cache_clear()
    api._RL.clear()
    runtime.reset()
    yield TestClient(api.app), monkeypatch
    cfg.get_settings.cache_clear()
    api._RL.clear()
    runtime.reset()


def _pd_sig(secret: str, body: bytes) -> str:
    return "v1=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


def _slack_sig(secret: str, ts: str, body: bytes) -> str:
    base = b"v0:" + ts.encode() + b":" + body
    return "v0=" + hmac.new(secret.encode(), base, hashlib.sha256).hexdigest()


# --- pure helpers --------------------------------------------------------- #
def test_pagerduty_parse_incident():
    inc = pd.parse_incident({"event": {"event_type": "incident.triggered", "data": {
        "id": "PINC1", "title": "checkout 5xx", "service": {"summary": "checkout-svc"}}}})
    assert inc and inc["id"] == "PINC1" and inc["service"] == "checkout-svc"
    assert pd.parse_incident({"event": {"event_type": "service.updated"}}) is None


def test_pagerduty_verify_signature():
    body = b'{"x":1}'
    assert pd.verify_signature("sek", body, _pd_sig("sek", body))
    assert not pd.verify_signature("sek", body, "v1=deadbeef")
    assert not pd.verify_signature("", body, _pd_sig("sek", body))


def test_slack_verify_signature_and_replay_window():
    body = b"payload=%7B%7D"
    ts = str(int(time.time()))
    assert slack.verify_signature("shh", ts, body, _slack_sig("shh", ts, body))
    assert not slack.verify_signature("shh", ts, body, "v0=bad")
    old = str(int(time.time()) - 1000)  # outside the 5-min window
    assert not slack.verify_signature("shh", old, body, _slack_sig("shh", old, body))


# --- endpoints ------------------------------------------------------------ #
def test_pagerduty_webhook_requires_valid_signature(client):
    c, mp = client
    mp.setenv("PAGERDUTY_WEBHOOK_SECRET", "whsec")
    cfg.get_settings.cache_clear()
    body = json.dumps({"event": {"event_type": "incident.triggered",
                                 "data": {"id": "PINC9", "title": "checkout 5xx"}}}).encode()
    assert c.post("/webhooks/pagerduty", content=body,
                  headers={"X-PagerDuty-Signature": "v1=bad"}).status_code == 401
    r = c.post("/webhooks/pagerduty", content=body,
               headers={"X-PagerDuty-Signature": _pd_sig("whsec", body)})
    assert r.status_code == 200 and r.json()["status"] == "accepted_no_llm"


def test_pagerduty_webhook_rejects_without_secret(client):
    c, _ = client
    assert c.post("/webhooks/pagerduty", content=b"{}",
                  headers={"X-PagerDuty-Signature": "v1=x"}).status_code == 401


def test_slack_interactions_requires_valid_signature(client):
    c, mp = client
    mp.setenv("SLACK_SIGNING_SECRET", "shh")
    cfg.get_settings.cache_clear()
    body = ("payload=" + json.dumps({"actions": [{"action_id": "approve", "value": "pd-1"}]})).encode()
    ts = str(int(time.time()))
    ok = c.post("/webhooks/slack/interactions", content=body, headers={
        "X-Slack-Request-Timestamp": ts, "X-Slack-Signature": _slack_sig("shh", ts, body)})
    assert ok.status_code == 200 and "Approved" in ok.json()["text"]
    bad = c.post("/webhooks/slack/interactions", content=body, headers={
        "X-Slack-Request-Timestamp": ts, "X-Slack-Signature": "v0=bad"})
    assert bad.status_code == 401
