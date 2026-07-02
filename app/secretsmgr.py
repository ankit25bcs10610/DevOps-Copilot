"""Secrets provider abstraction — resolve secrets from a manager, not just env vars.

Production shouldn't keep API keys and the vault KEK in plain env vars. This resolves
a secret name through a configured provider, falling back to the environment:

  - env   (default): os.environ — the local/dev path, unchanged.
  - aws:   AWS Secrets Manager (boto3), lazily imported.
  - vault: HashiCorp Vault KV v2 (hvac), lazily imported.

Selected via COPILOT_SECRETS_PROVIDER. Clients are injected in tests, so the AWS/
Vault backends are covered without any live service. The k8s-native path (External
Secrets Operator projecting into a Secret consumed by the Helm chart) needs no code —
this is for in-process resolution when you prefer it.
"""

from __future__ import annotations

import logging
import os
from functools import lru_cache
from typing import Any, Protocol

log = logging.getLogger("devcopilot.secretsmgr")


class SecretsProvider(Protocol):
    def get(self, name: str) -> str | None: ...


class EnvSecretsProvider:
    """Resolve from the process environment (default)."""

    def get(self, name: str) -> str | None:
        return os.environ.get(name)


class AwsSecretsManagerProvider:
    """AWS Secrets Manager. Secret names are looked up verbatim; a plaintext
    SecretString is returned as-is (JSON blobs are the caller's concern)."""

    def __init__(self, client: Any = None, prefix: str = ""):
        self._client = client
        self._prefix = prefix

    def _c(self) -> Any:
        if self._client is None:
            import boto3  # lazy optional dependency

            self._client = boto3.client("secretsmanager")
        return self._client

    def get(self, name: str) -> str | None:
        try:
            resp = self._c().get_secret_value(SecretId=f"{self._prefix}{name}")
            return resp.get("SecretString")
        except Exception:  # noqa: BLE001 — missing/inaccessible secret -> fall back
            log.debug("aws secret %s not resolved", name, exc_info=True)
            return None


class VaultProvider:
    """HashiCorp Vault KV v2. Reads `mount/data/<path>` and returns data[name]."""

    def __init__(self, client: Any = None, mount: str = "secret", path: str = "devops-copilot"):
        self._client = client
        self._mount = mount
        self._path = path

    def _c(self) -> Any:
        if self._client is None:
            import hvac  # lazy optional dependency

            self._client = hvac.Client(url=os.environ.get("VAULT_ADDR", ""),
                                       token=os.environ.get("VAULT_TOKEN", ""))
        return self._client

    def get(self, name: str) -> str | None:
        try:
            resp = self._c().secrets.kv.v2.read_secret_version(path=self._path, mount_point=self._mount)
            return resp["data"]["data"].get(name)
        except Exception:  # noqa: BLE001
            log.debug("vault secret %s not resolved", name, exc_info=True)
            return None


def _make_provider(kind: str) -> SecretsProvider:
    kind = (kind or "env").strip().lower()
    if kind == "aws":
        return AwsSecretsManagerProvider(prefix=os.environ.get("COPILOT_SECRETS_PREFIX", ""))
    if kind == "vault":
        return VaultProvider()
    return EnvSecretsProvider()


@lru_cache(maxsize=1)
def _provider() -> SecretsProvider:
    return _make_provider(os.environ.get("COPILOT_SECRETS_PROVIDER", "env"))


def get_secret(name: str, default: str = "") -> str:
    """Resolve a secret via the configured provider, falling back to the environment,
    then to `default`. Non-env providers still fall back to env so a partial rollout
    (some secrets in the manager, some in env) works."""
    val = _provider().get(name)
    if val is None and not isinstance(_provider(), EnvSecretsProvider):
        val = os.environ.get(name)
    return val if val is not None else default


def resolve(name: str, default: str = "") -> str:
    """Alias for get_secret (readable at call sites)."""
    return get_secret(name, default)
