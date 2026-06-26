"""Request-scoped tenant context (the multi-tenancy seam).

Every later commercialization phase is built behind this contextvar so per-tenant
config/credentials are resolved from the request — never from a process global —
and isolation is provable (deny-by-default: no context ⇒ no tenant). When no
tenant is set (single-tenant / offline demo) everything falls back to the existing
runtime.py + Settings path, byte-for-byte.

Mirrors the proven request_id_var set/reset pattern in app/observability.py.
"""

from __future__ import annotations

import contextvars
from dataclasses import dataclass, field
from typing import Any, Awaitable


@dataclass(frozen=True)
class TenantConfig:
    """Immutable per-request view of a tenant's config + decrypted credentials.
    Built by the auth layer from the TenantStore; read by runtime.py accessors."""

    org_id: str
    plan: str = "free"
    role: str = "viewer"
    provider: str = ""
    model: str = ""
    fast_model: str = ""
    # provider -> api key (LLM keys), e.g. {"anthropic": "sk-..."}
    provider_keys: dict[str, str] = field(default_factory=dict)
    github_token: str = ""
    github_repo: str = ""
    repo_path: str = ""
    logs_path: str = ""
    # env-style integration secrets injected into MCP subprocesses
    # (DD_API_KEY, PAGERDUTY_API_TOKEN, SENTRY_API_TOKEN, KUBE_CONFIG_PATH, …).
    integration_secrets: dict[str, str] = field(default_factory=dict)


# None ⇒ single-tenant / no tenant resolved (deny-by-default for tenant data).
current_tenant: contextvars.ContextVar[TenantConfig | None] = contextvars.ContextVar(
    "current_tenant", default=None
)
# Who is acting (api-key id / user email) — stamped onto audit lines.
actor_var: contextvars.ContextVar[str] = contextvars.ContextVar("actor", default="-")


def set_tenant(cfg: TenantConfig | None) -> contextvars.Token:
    return current_tenant.set(cfg)


def reset_tenant(token: contextvars.Token) -> None:
    current_tenant.reset(token)


def get_tenant() -> TenantConfig | None:
    return current_tenant.get()


def tenant_id() -> str:
    cfg = current_tenant.get()
    return cfg.org_id if cfg else "-"


def set_actor(actor: str) -> contextvars.Token:
    return actor_var.set(actor or "-")


def get_actor() -> str:
    return actor_var.get()


async def run_with_tenant(cfg: TenantConfig | None, coro: Awaitable[Any]) -> Any:
    """Run a coroutine with `cfg` established as the current tenant, then restore.

    Needed for background tasks (asyncio.create_task on the webhook path): a task
    captures the context at creation, but if the request already reset its
    contextvar by the time the task runs, the tenant would be lost — so we
    re-establish it explicitly inside the task.
    """
    token = set_tenant(cfg)
    try:
        return await coro
    finally:
        reset_tenant(token)
