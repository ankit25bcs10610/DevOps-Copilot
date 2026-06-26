"""Supabase JWT verification against a JWKS (self-signed keypair, no network)."""

import json
import time

import pytest

jwt = pytest.importorskip("jwt")  # PyJWT
pytest.importorskip("cryptography")

from app.tenancy import supabase_auth  # noqa: E402


def _keypair_and_jwks():
    from cryptography.hazmat.primitives.asymmetric import rsa

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pub_jwk = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(key.public_key()))
    pub_jwk.update(kid="test-kid", alg="RS256", use="sig")
    return key, {"keys": [pub_jwk]}


def _token(key, **claims):
    payload = {"exp": int(time.time()) + 3600, **claims}
    return jwt.encode(payload, key, algorithm="RS256", headers={"kid": "test-kid"})


def test_decode_with_jwks_roundtrip():
    key, jwks = _keypair_and_jwks()
    token = _token(key, sub="u1", email="a@acme.com", aud="authenticated")
    claims = supabase_auth.decode_with_jwks(token, jwks, audience="authenticated")
    assert claims["email"] == "a@acme.com"
    assert claims["sub"] == "u1"


def test_decode_rejects_wrong_audience():
    key, jwks = _keypair_and_jwks()
    token = _token(key, email="a@acme.com", aud="other-service")
    with pytest.raises(Exception):  # noqa: B017 — InvalidAudienceError
        supabase_auth.decode_with_jwks(token, jwks, audience="authenticated")


def test_decode_rejects_token_signed_by_a_different_key():
    key1, _ = _keypair_and_jwks()
    _, jwks2 = _keypair_and_jwks()  # different keypair's public JWKS
    token = _token(key1, email="a@acme.com", aud="authenticated")
    with pytest.raises(Exception):  # noqa: B017 — signature can't be verified
        supabase_auth.decode_with_jwks(token, jwks2, audience="authenticated")


def test_verify_jwt_disabled_returns_none(monkeypatch):
    import app.config as cfg

    monkeypatch.delenv("SUPABASE_JWKS_URL", raising=False)
    cfg.get_settings.cache_clear()
    assert supabase_auth.verify_jwt("a.b.c") is None
    cfg.get_settings.cache_clear()
