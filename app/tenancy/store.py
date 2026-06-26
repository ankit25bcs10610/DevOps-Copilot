"""Async tenant store (aiosqlite).

Persists orgs, members, API keys (hashed), per-tenant integration secrets
(encrypted via the Fernet vault), and usage records. SQLite by default so the
whole commercial layer runs as a single artifact; a Postgres URL is the localized
swap for multi-instance production (raises clearly until that impl is added).

API keys are stored as `dcp_<prefix>_<secret>`: only the prefix (for lookup) and a
SHA-256 of the secret are persisted, so a store dump never leaks usable keys.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import secrets
import time
import uuid

from app import secrets_vault
from app.tenancy.models import (
    ApiKey,
    Membership,
    Org,
    User,
    normalize_plan,
    normalize_role,
)

_KEY_PREFIX = "dcp"


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _now_plus_days(days: int) -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() + days * 86400))


# Default API-key lifetime (days). Permanent keys are a top 2026 anti-pattern.
_DEFAULT_KEY_TTL_DAYS = 90


def _id() -> str:
    return uuid.uuid4().hex


def _hash_secret(secret: str) -> str:
    return hashlib.sha256(secret.encode()).hexdigest()


_SCHEMA = """
CREATE TABLE IF NOT EXISTS orgs (
    id TEXT PRIMARY KEY, name TEXT NOT NULL, plan TEXT NOT NULL,
    created_at TEXT NOT NULL, stripe_customer_id TEXT DEFAULT ''
);
CREATE TABLE IF NOT EXISTS users (
    id TEXT PRIMARY KEY, email TEXT UNIQUE NOT NULL, created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS memberships (
    org_id TEXT NOT NULL, user_id TEXT NOT NULL, role TEXT NOT NULL,
    PRIMARY KEY (org_id, user_id)
);
CREATE TABLE IF NOT EXISTS api_keys (
    id TEXT PRIMARY KEY, org_id TEXT NOT NULL, prefix TEXT UNIQUE NOT NULL,
    secret_hash TEXT NOT NULL, name TEXT DEFAULT '', role TEXT NOT NULL,
    created_at TEXT NOT NULL, last_used_at TEXT DEFAULT '', revoked_at TEXT DEFAULT '',
    expires_at TEXT DEFAULT ''
);
CREATE TABLE IF NOT EXISTS integration_secrets (
    org_id TEXT NOT NULL, name TEXT NOT NULL, value_encrypted TEXT NOT NULL,
    updated_at TEXT NOT NULL, PRIMARY KEY (org_id, name)
);
CREATE TABLE IF NOT EXISTS usage (
    id TEXT PRIMARY KEY, org_id TEXT NOT NULL, kind TEXT NOT NULL,
    amount INTEGER NOT NULL, ts TEXT NOT NULL, meta TEXT DEFAULT '{}',
    event_key TEXT DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_usage_org_kind_ts ON usage (org_id, kind, ts);
-- Idempotency: a non-empty event_key is unique, so a retried/replayed turn is
-- recorded at most once (prevents double-billing). Empty keys are unconstrained.
CREATE UNIQUE INDEX IF NOT EXISTS idx_usage_event_key ON usage (event_key) WHERE event_key != '';
"""


class TenantStore:
    """aiosqlite-backed tenant persistence."""

    def __init__(self, db_path: str):
        if db_path.startswith(("postgres://", "postgresql://")):
            raise RuntimeError(
                "Postgres tenant store isn't implemented yet — set COPILOT_TENANT_DB "
                "to a SQLite path, or add an asyncpg-backed TenantStore for production."
            )
        self.db_path = db_path

    async def setup(self) -> None:
        import aiosqlite

        async with aiosqlite.connect(self.db_path) as db:
            await db.executescript(_SCHEMA)
            await db.commit()

    # --- orgs / users / members ------------------------------------------- #
    async def create_org(self, name: str, plan: str = "free", owner_email: str = "") -> Org:
        org = Org(id=_id(), name=name, plan=normalize_plan(plan), created_at=_now())
        import aiosqlite

        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                "INSERT INTO orgs (id, name, plan, created_at) VALUES (?,?,?,?)",
                (org.id, org.name, org.plan, org.created_at),
            )
            await db.commit()
        if owner_email:
            user = await self.create_user(owner_email)
            await self.add_member(org.id, user.id, "owner")
        return org

    async def get_org(self, org_id: str) -> Org | None:
        import aiosqlite

        async with aiosqlite.connect(self.db_path) as db:
            async with db.execute(
                "SELECT id, name, plan, created_at, stripe_customer_id FROM orgs WHERE id=?",
                (org_id,),
            ) as cur:
                row = await cur.fetchone()
        return Org(*row) if row else None

    async def set_plan(self, org_id: str, plan: str) -> None:
        import aiosqlite

        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("UPDATE orgs SET plan=? WHERE id=?", (normalize_plan(plan), org_id))
            await db.commit()

    async def create_user(self, email: str) -> User:
        import aiosqlite

        existing = await self.get_user_by_email(email)
        if existing:
            return existing
        user = User(id=_id(), email=email.strip().lower(), created_at=_now())
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                "INSERT INTO users (id, email, created_at) VALUES (?,?,?)",
                (user.id, user.email, user.created_at),
            )
            await db.commit()
        return user

    async def get_user_by_email(self, email: str) -> User | None:
        import aiosqlite

        async with aiosqlite.connect(self.db_path) as db:
            async with db.execute(
                "SELECT id, email, created_at FROM users WHERE email=?", (email.strip().lower(),)
            ) as cur:
                row = await cur.fetchone()
        return User(*row) if row else None

    async def add_member(self, org_id: str, user_id: str, role: str) -> Membership:
        m = Membership(org_id=org_id, user_id=user_id, role=normalize_role(role))
        import aiosqlite

        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                "INSERT OR REPLACE INTO memberships (org_id, user_id, role) VALUES (?,?,?)",
                (m.org_id, m.user_id, m.role),
            )
            await db.commit()
        return m

    async def count_members(self, org_id: str) -> int:
        import aiosqlite

        async with aiosqlite.connect(self.db_path) as db:
            async with db.execute(
                "SELECT COUNT(*) FROM memberships WHERE org_id=?", (org_id,)
            ) as cur:
                row = await cur.fetchone()
        return int(row[0]) if row else 0

    # --- API keys --------------------------------------------------------- #
    async def issue_api_key(self, org_id: str, name: str = "", role: str = "responder",
                            ttl_days: int = _DEFAULT_KEY_TTL_DAYS) -> tuple[str, ApiKey]:
        """Create a key; returns (plaintext_key, record). The plaintext is shown ONCE.
        Keys expire after ttl_days (0 = never) — rotate before expiry."""
        prefix = secrets.token_hex(4)
        raw = secrets.token_hex(24)
        plaintext = f"{_KEY_PREFIX}_{prefix}_{raw}"
        expires_at = _now_plus_days(ttl_days) if ttl_days > 0 else ""
        rec = ApiKey(id=_id(), org_id=org_id, prefix=prefix, name=name,
                     role=normalize_role(role), created_at=_now(), expires_at=expires_at)
        import aiosqlite

        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                "INSERT INTO api_keys (id, org_id, prefix, secret_hash, name, role, created_at, expires_at) "
                "VALUES (?,?,?,?,?,?,?,?)",
                (rec.id, org_id, prefix, _hash_secret(raw), name, rec.role, rec.created_at, expires_at),
            )
            await db.commit()
        return plaintext, rec

    async def resolve_api_key(self, plaintext: str) -> tuple[Org, ApiKey] | None:
        """Validate a presented key (constant-time) and return (org, key) if active."""
        parts = (plaintext or "").split("_")
        if len(parts) != 3 or parts[0] != _KEY_PREFIX:
            return None
        _, prefix, raw = parts
        import aiosqlite

        async with aiosqlite.connect(self.db_path) as db:
            async with db.execute(
                "SELECT id, org_id, prefix, secret_hash, name, role, created_at, last_used_at, "
                "revoked_at, expires_at FROM api_keys WHERE prefix=?",
                (prefix,),
            ) as cur:
                row = await cur.fetchone()
            if not row:
                return None
            if not hmac.compare_digest(row[3], _hash_secret(raw)):
                return None
            if row[8]:  # revoked_at
                return None
            if row[9] and row[9] <= _now():  # expired
                return None
            await db.execute("UPDATE api_keys SET last_used_at=? WHERE id=?", (_now(), row[0]))
            await db.commit()
        key = ApiKey(id=row[0], org_id=row[1], prefix=row[2], name=row[4], role=row[5],
                     created_at=row[6], last_used_at=_now(), revoked_at=row[8], expires_at=row[9])
        org = await self.get_org(key.org_id)
        return (org, key) if org else None

    async def revoke_api_key(self, key_id: str, org_id: str = "") -> bool:
        """Revoke a key. When org_id is given the UPDATE is org-scoped, so a tenant
        can never revoke another org's key. Returns True if a key was revoked."""
        import aiosqlite

        sql = "UPDATE api_keys SET revoked_at=? WHERE id=? AND revoked_at=''"
        params: list = [_now(), key_id]
        if org_id:
            sql += " AND org_id=?"
            params.append(org_id)
        async with aiosqlite.connect(self.db_path) as db:
            cur = await db.execute(sql, params)
            await db.commit()
            return cur.rowcount > 0

    async def list_api_keys(self, org_id: str) -> list[ApiKey]:
        """List an org's keys (metadata only — never the secret)."""
        import aiosqlite

        async with aiosqlite.connect(self.db_path) as db:
            async with db.execute(
                "SELECT id, org_id, prefix, name, role, created_at, last_used_at, revoked_at "
                "FROM api_keys WHERE org_id=? ORDER BY created_at DESC",
                (org_id,),
            ) as cur:
                rows = await cur.fetchall()
        return [
            ApiKey(id=r[0], org_id=r[1], prefix=r[2], name=r[3], role=r[4],
                   created_at=r[5], last_used_at=r[6], revoked_at=r[7])
            for r in rows
        ]

    async def count_active_api_keys(self, org_id: str) -> int:
        import aiosqlite

        async with aiosqlite.connect(self.db_path) as db:
            async with db.execute(
                "SELECT COUNT(*) FROM api_keys WHERE org_id=? AND revoked_at=''", (org_id,)
            ) as cur:
                row = await cur.fetchone()
        return int(row[0]) if row else 0

    # --- per-tenant integration secrets (encrypted at rest) --------------- #
    async def set_integration_secret(self, org_id: str, name: str, value: str) -> None:
        token = secrets_vault.encrypt(value)
        import aiosqlite

        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                "INSERT OR REPLACE INTO integration_secrets (org_id, name, value_encrypted, updated_at) "
                "VALUES (?,?,?,?)",
                (org_id, name, token, _now()),
            )
            await db.commit()

    async def get_integration_secret(self, org_id: str, name: str) -> str | None:
        import aiosqlite

        async with aiosqlite.connect(self.db_path) as db:
            async with db.execute(
                "SELECT value_encrypted FROM integration_secrets WHERE org_id=? AND name=?",
                (org_id, name),
            ) as cur:
                row = await cur.fetchone()
        if not row:
            return None
        return secrets_vault.decrypt(row[0])

    async def get_integration_secrets(self, org_id: str) -> dict[str, str]:
        """All of an org's integration secrets, decrypted (for building a session)."""
        import aiosqlite

        async with aiosqlite.connect(self.db_path) as db:
            async with db.execute(
                "SELECT name, value_encrypted FROM integration_secrets WHERE org_id=?", (org_id,)
            ) as cur:
                rows = await cur.fetchall()
        return {name: secrets_vault.decrypt(tok) for name, tok in rows}

    async def count_integrations(self, org_id: str) -> int:
        import aiosqlite

        async with aiosqlite.connect(self.db_path) as db:
            async with db.execute(
                "SELECT COUNT(*) FROM integration_secrets WHERE org_id=?", (org_id,)
            ) as cur:
                row = await cur.fetchone()
        return int(row[0]) if row else 0

    # --- usage metering --------------------------------------------------- #
    async def record_usage(self, org_id: str, kind: str, amount: int = 1,
                           meta: dict | None = None, event_key: str = "") -> None:
        """Append a usage event. A non-empty event_key is idempotent (INSERT OR
        IGNORE on the unique index), so a retried/replayed turn isn't double-counted."""
        import aiosqlite

        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                "INSERT OR IGNORE INTO usage (id, org_id, kind, amount, ts, meta, event_key) "
                "VALUES (?,?,?,?,?,?,?)",
                (_id(), org_id, kind, int(amount), _now(),
                 json.dumps(meta or {}, default=str), event_key),
            )
            await db.commit()

    async def usage_total(self, org_id: str, kind: str, since: str = "") -> int:
        import aiosqlite

        q = "SELECT COALESCE(SUM(amount),0) FROM usage WHERE org_id=? AND kind=?"
        params: list = [org_id, kind]
        if since:
            q += " AND ts>=?"
            params.append(since)
        async with aiosqlite.connect(self.db_path) as db:
            async with db.execute(q, params) as cur:
                row = await cur.fetchone()
        return int(row[0]) if row else 0


def month_start() -> str:
    """First instant of the current UTC month (for monthly quota windows)."""
    return time.strftime("%Y-%m-01T00:00:00Z", time.gmtime())
