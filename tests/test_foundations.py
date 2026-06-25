"""Multi-tenant foundations: audit trail + secret-vault encryption roundtrip."""

import importlib.util
import logging

import pytest

import app.config as cfg
from app import audit, secrets_vault

_HAS_CRYPTO = importlib.util.find_spec("cryptography") is not None


def test_audit_record_emits_structured_event(caplog):
    with caplog.at_level(logging.INFO, logger="devcopilot.audit"):
        audit.record("approval.decided", thread="t1", approved=True)
    assert any("approval.decided" in r.getMessage() and "t1" in r.getMessage() for r in caplog.records)


@pytest.mark.skipif(not _HAS_CRYPTO, reason="cryptography not installed")
def test_secret_vault_roundtrip(monkeypatch):
    from cryptography.fernet import Fernet

    monkeypatch.setenv("COPILOT_SECRET_KEY", Fernet.generate_key().decode())
    cfg.get_settings.cache_clear()
    secrets_vault._fernet.cache_clear()
    try:
        token = secrets_vault.encrypt("sk-super-secret")
        assert token != "sk-super-secret"
        assert secrets_vault.decrypt(token) == "sk-super-secret"
    finally:
        secrets_vault._fernet.cache_clear()
        cfg.get_settings.cache_clear()
