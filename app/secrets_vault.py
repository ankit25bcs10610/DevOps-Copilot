"""Per-tenant secret vault primitive — symmetric encryption for secrets at rest.

The foundation for storing each tenant's integration credentials (Datadog,
GitHub, PagerDuty, LLM keys) encrypted in a database rather than in process
globals. Uses Fernet (AES-128-CBC + HMAC) keyed by `COPILOT_SECRET_KEY`.

This is the encryption primitive; wiring it to a per-tenant store is part of the
multi-tenant work. `cryptography` is an optional dependency — install it (or the
`secrets` extra) to use the vault.
"""

from __future__ import annotations

import logging
from functools import lru_cache

from app.config import get_settings

log = logging.getLogger("devcopilot.secrets")


@lru_cache(maxsize=1)
def _fernet():
    try:
        from cryptography.fernet import Fernet
    except ImportError as exc:  # noqa: TRY003
        raise RuntimeError(
            "The secret vault requires 'cryptography' (pip install cryptography)."
        ) from exc

    key = get_settings().copilot_secret_key.strip()
    if not key:
        log.warning(
            "COPILOT_SECRET_KEY not set — generating an ephemeral vault key; "
            "encrypted secrets will NOT survive a restart. Set one in production."
        )
        key = Fernet.generate_key().decode()
    return Fernet(key.encode())


def encrypt(plaintext: str) -> str:
    """Encrypt a secret with the KEK (COPILOT_SECRET_KEY). Returns a token string."""
    return _fernet().encrypt(plaintext.encode()).decode()


def decrypt(token: str) -> str:
    """Decrypt a token produced by encrypt()."""
    return _fernet().decrypt(token.encode()).decode()


# --------------------------------------------------------------------------- #
# Envelope encryption: a per-tenant Data Encryption Key (DEK) wrapped by the
# COPILOT_SECRET_KEY Key Encryption Key (KEK). Each tenant's secrets are encrypted
# with their own DEK, so compromising one DEK never exposes another tenant, and
# deleting a DEK ("crypto-shred") makes that tenant's secrets unrecoverable —
# the basis for cryptographic isolation + GDPR-style erasure.
# --------------------------------------------------------------------------- #
def new_wrapped_dek() -> str:
    """Generate a fresh DEK and return it wrapped by the KEK (safe to store)."""
    from cryptography.fernet import Fernet

    dek = Fernet.generate_key().decode()
    return encrypt(dek)  # wrap with the KEK


def _dek_fernet(wrapped_dek: str):
    from cryptography.fernet import Fernet

    return Fernet(decrypt(wrapped_dek).encode())  # unwrap with the KEK


def encrypt_with(wrapped_dek: str, plaintext: str) -> str:
    """Encrypt a secret with a tenant's (wrapped) DEK."""
    return _dek_fernet(wrapped_dek).encrypt(plaintext.encode()).decode()


def decrypt_with(wrapped_dek: str, token: str) -> str:
    """Decrypt a token produced by encrypt_with() for the same wrapped DEK."""
    return _dek_fernet(wrapped_dek).decrypt(token.encode()).decode()


def rotate_dek(tokens: dict[str, str], old_wrapped_dek: str) -> tuple[str, dict[str, str]]:
    """Rotate a tenant's DEK: decrypt every token under the OLD DEK, mint a fresh
    wrapped DEK, and re-encrypt everything under it. Returns (new_wrapped_dek,
    re-encrypted tokens). Limits the blast radius of a leaked DEK and lets a tenant
    re-key on demand. Pure over its inputs (no store), so it's unit-testable."""
    new_wrapped = new_wrapped_dek()
    out: dict[str, str] = {}
    for name, tok in tokens.items():
        plain = decrypt_with(old_wrapped_dek, tok) if old_wrapped_dek else decrypt(tok)
        out[name] = encrypt_with(new_wrapped, plain)
    return new_wrapped, out
