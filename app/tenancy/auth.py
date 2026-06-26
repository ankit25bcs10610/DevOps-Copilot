"""Multi-tenant auth wiring: resolve a presented API key into a TenantConfig.

Bridges the TenantStore (orgs + hashed keys + encrypted secrets) to the request
contextvar the rest of the app reads (app/tenant_context.py). Keeps the secret
layout in one place: per-provider LLM keys, GitHub creds, and the env-style
integration secrets that get injected into MCP subprocesses.
"""

from __future__ import annotations

from app.config import get_settings
from app.tenancy.models import Org
from app.tenancy.store import TenantStore
from app.tenant_context import TenantConfig

# secret name (as stored per-tenant) -> LLM provider it unlocks.
_LLM_KEY_ENV = {
    "anthropic": "ANTHROPIC_API_KEY",
    "openai": "OPENAI_API_KEY",
    "gemini": "GEMINI_API_KEY",
    "groq": "GROQ_API_KEY",
    "deepseek": "DEEPSEEK_API_KEY",
}
# env-style secrets forwarded verbatim to MCP subprocesses.
_INTEGRATION_KEYS = frozenset({
    "DD_API_KEY", "DD_APP_KEY", "DD_SITE",
    "PAGERDUTY_API_TOKEN", "PAGERDUTY_FROM_EMAIL",
    "KUBE_CONFIG_PATH", "KUBE_NAMESPACE",
    "SENTRY_API_TOKEN", "SENTRY_ORG", "SENTRY_PROJECT",
    "TRACES_API_URL", "CORPUS_PATH",
})

_store: TenantStore | None = None


def get_store() -> TenantStore:
    global _store
    if _store is None:
        _store = TenantStore(get_settings().copilot_tenant_db)
    return _store


def reset_store_cache() -> None:
    """Drop the cached store (tests point COPILOT_TENANT_DB at a tmp file)."""
    global _store
    _store = None


def build_tenant_config(org: Org, role: str, secrets: dict[str, str]) -> TenantConfig:
    """Map an org + its decrypted secrets into the request's TenantConfig."""
    provider_keys = {p: secrets[env] for p, env in _LLM_KEY_ENV.items() if secrets.get(env)}
    integration = {k: v for k, v in secrets.items() if k in _INTEGRATION_KEYS}
    return TenantConfig(
        org_id=org.id,
        plan=org.plan,
        role=role,
        provider=secrets.get("COPILOT_PROVIDER", ""),
        model=secrets.get("COPILOT_MODEL", ""),
        fast_model=secrets.get("COPILOT_FAST_MODEL", ""),
        provider_keys=provider_keys,
        github_token=secrets.get("GITHUB_TOKEN", ""),
        github_repo=secrets.get("GITHUB_REPO", ""),
        integration_secrets=integration,
    )


async def resolve(api_key: str) -> tuple[TenantConfig, str] | None:
    """Validate a presented `dcp_…` key and return (TenantConfig, actor) or None."""
    store = get_store()
    resolved = await store.resolve_api_key(api_key)
    if not resolved:
        return None
    org, key = resolved
    secrets = await store.get_integration_secrets(org.id)
    cfg = build_tenant_config(org, key.role, secrets)
    return cfg, f"key:{key.id[:8]}"


async def resolve_jwt(token: str) -> tuple[TenantConfig, str] | None:
    """Validate a Supabase/SSO JWT and map it to a TenantConfig via the user's
    org membership. Returns (config, actor) or None (bad token / not a member)."""
    from app.tenancy import supabase_auth

    claims = supabase_auth.verify_jwt(token)
    if not claims:
        return None
    email = (claims.get("email") or "").strip().lower()
    if not email:
        return None
    store = get_store()
    membership = await store.get_membership_by_email(email)
    if not membership:
        return None  # authenticated, but not a member of any org
    org_id, role = membership
    org = await store.get_org(org_id)
    if not org:
        return None
    secrets = await store.get_integration_secrets(org_id)
    return build_tenant_config(org, role, secrets), f"user:{email}"
