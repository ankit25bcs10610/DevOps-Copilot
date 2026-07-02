"""Secrets provider abstraction — env default + AWS/Vault backends (fake clients)."""

from app.secretsmgr import (
    AwsSecretsManagerProvider,
    EnvSecretsProvider,
    VaultProvider,
    _make_provider,
    get_secret,
)


def test_env_provider(monkeypatch):
    monkeypatch.setenv("MY_SECRET", "from-env")
    assert EnvSecretsProvider().get("MY_SECRET") == "from-env"
    assert EnvSecretsProvider().get("MISSING") is None


def test_aws_provider_with_fake_client():
    class _Fake:
        def get_secret_value(self, SecretId):
            assert SecretId == "prod/ANTHROPIC_API_KEY"
            return {"SecretString": "sk-from-aws"}

    p = AwsSecretsManagerProvider(client=_Fake(), prefix="prod/")
    assert p.get("ANTHROPIC_API_KEY") == "sk-from-aws"


def test_aws_provider_missing_returns_none():
    class _Fake:
        def get_secret_value(self, SecretId):
            raise RuntimeError("ResourceNotFoundException")

    assert AwsSecretsManagerProvider(client=_Fake()).get("NOPE") is None


def test_vault_provider_with_fake_client():
    class _KV:
        def read_secret_version(self, path, mount_point):
            return {"data": {"data": {"ANTHROPIC_API_KEY": "sk-from-vault"}}}

    class _Fake:
        class secrets:
            class kv:
                v2 = _KV()

    assert VaultProvider(client=_Fake()).get("ANTHROPIC_API_KEY") == "sk-from-vault"


def test_factory_selects_provider(monkeypatch):
    assert isinstance(_make_provider("env"), EnvSecretsProvider)
    assert isinstance(_make_provider("aws"), AwsSecretsManagerProvider)
    assert isinstance(_make_provider("vault"), VaultProvider)
    assert isinstance(_make_provider(""), EnvSecretsProvider)  # default


def test_get_secret_falls_back_to_env_and_default(monkeypatch):
    import app.secretsmgr as sm

    monkeypatch.setattr(sm, "_provider", lambda: EnvSecretsProvider())
    monkeypatch.setenv("PRESENT", "yes")
    assert get_secret("PRESENT") == "yes"
    assert get_secret("ABSENT", default="fallback") == "fallback"
