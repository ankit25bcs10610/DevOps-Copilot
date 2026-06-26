"""Runtime override store: per-provider keys, model resolution, snapshot/restore,
and tenant-context awareness (multi-tenant config isolation)."""

from app import runtime
from app.tenant_context import TenantConfig, reset_tenant, set_tenant


def test_set_model_records_provider_key_and_models():
    runtime.reset()
    runtime.set_model("openai", "sk-test", "gpt-4o", "gpt-4o-mini")
    assert runtime.provider() == "openai"
    assert runtime.provider_key("openai") == "sk-test"
    assert runtime.model_override() == "gpt-4o"
    assert runtime.fast_model_override() == "gpt-4o-mini"
    runtime.reset()


def test_keys_are_isolated_per_provider():
    runtime.reset()
    runtime.set_model("openai", "o-key", "", "")
    runtime.set_model("anthropic", "a-key", "", "")
    assert runtime.provider_key("openai") == "o-key"
    assert runtime.provider_key("anthropic") == "a-key"
    runtime.reset()


def test_snapshot_restore_roundtrip():
    runtime.reset()
    runtime.set_model("groq", "gk", "", "")
    snap = runtime.model_snapshot()
    runtime.set_model("anthropic", "ak", "", "")
    runtime.restore_model(snap)
    assert runtime.provider() == "groq"
    assert runtime.provider_key("groq") == "gk"
    runtime.reset()


def test_reset_clears_everything():
    runtime.set_model("deepseek", "dk", "deepseek-chat", "")
    runtime.reset()
    assert runtime.provider() == "anthropic"  # back to .env / default
    assert runtime.provider_key("deepseek") == ""


def test_tenant_context_overrides_globals():
    runtime.reset()
    # A process-global override is set (single-tenant style)...
    runtime.set_model("openai", "global-key", "gpt-4o", "")
    # ...but inside a tenant context, that tenant's config wins and the global
    # key is NOT leaked to the tenant.
    cfg = TenantConfig(
        org_id="acme", provider="anthropic", model="claude-opus-4-8",
        provider_keys={"anthropic": "tenant-anthropic-key"},
        github_token="ght-acme", github_repo="acme/app",
    )
    token = set_tenant(cfg)
    try:
        assert runtime.provider() == "anthropic"
        assert runtime.provider_key("anthropic") == "tenant-anthropic-key"
        assert runtime.provider_key("openai") == ""  # global key NOT visible to tenant
        assert runtime.model_override() == "claude-opus-4-8"
        assert runtime.github_token() == "ght-acme"
        assert runtime.github_repo() == "acme/app"
    finally:
        reset_tenant(token)
    # Outside the context, the global override is intact (no bleed).
    assert runtime.provider() == "openai"
    assert runtime.provider_key("openai") == "global-key"
    runtime.reset()


def test_two_tenants_see_their_own_config():
    runtime.reset()
    a = TenantConfig(org_id="A", provider="anthropic", provider_keys={"anthropic": "A-key"})
    b = TenantConfig(org_id="B", provider="groq", provider_keys={"groq": "B-key"})
    ta = set_tenant(a)
    assert runtime.provider() == "anthropic" and runtime.provider_key() == "A-key"
    reset_tenant(ta)
    tb = set_tenant(b)
    try:
        assert runtime.provider() == "groq" and runtime.provider_key() == "B-key"
        assert runtime.provider_key("anthropic") == ""  # no bleed from A
    finally:
        reset_tenant(tb)
    runtime.reset()
