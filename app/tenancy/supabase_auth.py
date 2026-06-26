"""Supabase Auth — verify Supabase-issued JWTs against the project's JWKS.

Lets a user who logged in via Supabase authenticate to the multi-tenant API with
their `Bearer <jwt>`, mapped to their org/role membership. Verification uses only
the **public** JWKS (no Supabase secret), validates the signature + expiry +
audience, and restricts to asymmetric algorithms (RS256/ES256) to avoid alg-confusion.

`decode_with_jwks` is a pure helper (no network) so verification is unit-testable;
`verify_jwt` wraps it with a cached PyJWKClient that fetches the live JWKS.
"""

from __future__ import annotations

import logging

from app.config import get_settings

log = logging.getLogger("devcopilot.supabase")

# Asymmetric only — never allow HS256 here (would let a leaked public key forge tokens).
_ALGORITHMS = ["RS256", "ES256"]

_jwks_client = None


def reset_cache() -> None:
    """Drop the cached JWKS client (tests / key rotation)."""
    global _jwks_client
    _jwks_client = None


def _client():
    global _jwks_client
    if _jwks_client is None:
        import jwt

        _jwks_client = jwt.PyJWKClient(get_settings().supabase_jwks_url)
    return _jwks_client


def decode_with_jwks(token: str, jwks: dict, audience: str | None = None) -> dict:
    """Verify a JWT against an in-memory JWKS dict (pure; raises on any failure)."""
    import jwt

    jwk_set = jwt.PyJWKSet.from_dict(jwks)
    kid = jwt.get_unverified_header(token).get("kid")
    signing_key = next(k for k in jwk_set.keys if k.key_id == kid)
    return jwt.decode(
        token,
        signing_key.key,
        algorithms=_ALGORITHMS,
        audience=audience,
        options={"verify_aud": audience is not None},
    )


def verify_jwt(token: str) -> dict | None:
    """Verify a Supabase JWT against the live JWKS. Returns claims, or None on any
    failure (disabled / bad signature / expired / wrong audience)."""
    s = get_settings()
    if not s.supabase_jwks_url:
        return None
    try:
        import jwt

        signing_key = _client().get_signing_key_from_jwt(token).key
        return jwt.decode(
            token,
            signing_key,
            algorithms=_ALGORITHMS,
            audience=s.supabase_jwt_aud or None,
            options={"verify_aud": bool(s.supabase_jwt_aud)},
        )
    except Exception:  # noqa: BLE001 — any verification failure is an auth failure
        log.info("Supabase JWT verification failed", exc_info=True)
        return None
