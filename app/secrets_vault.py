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
    """Encrypt a secret for storage. Returns a URL-safe token string."""
    return _fernet().encrypt(plaintext.encode()).decode()


def decrypt(token: str) -> str:
    """Decrypt a token produced by encrypt()."""
    return _fernet().decrypt(token.encode()).decode()
