"""Mutable runtime configuration set from the UI at run time.

Holds overrides for the model/provider, GitHub credentials, and the repo/logs
data sources. These take precedence over the static .env values (Settings) so a
user can reconfigure the agent from the sidebar without restarting the server.
Stored in memory only — never written to disk.
"""

from __future__ import annotations

from pathlib import Path

from app.config import ROOT, get_settings

# Every provider the agent can target. Keep in sync with app/llm.py defaults,
# the API validation, and the frontend provider dropdown.
PROVIDERS: tuple[str, ...] = ("anthropic", "openai", "gemini", "groq", "deepseek")

_ov: dict[str, str] = {
    # model / provider
    "provider": "",
    "model": "",
    "fast_model": "",
    # github
    "github_token": "",
    "github_repo": "",
    # data sources
    "repo_path": "",
    "logs_path": "",
}

# Per-provider API key overrides — remembered separately so switching provider in
# the UI doesn't wipe the key you pasted for another one.
_keys: dict[str, str] = {p: "" for p in PROVIDERS}


def _resolve_path(p: str) -> Path:
    pp = Path(p).expanduser()
    return pp if pp.is_absolute() else (ROOT / pp)


# ---- model / provider ----
def provider() -> str:
    return (_ov["provider"] or get_settings().copilot_provider).lower()


def _settings_key(p: str) -> str:
    """The .env-configured key for a provider (fallback when no UI override)."""
    s = get_settings()
    return {
        "anthropic": s.anthropic_api_key,
        "openai": s.openai_api_key,
        "gemini": s.gemini_api_key,
        "groq": s.groq_api_key,
        "deepseek": s.deepseek_api_key,
    }.get(p, "")


def provider_key(p: str | None = None) -> str:
    """Resolve the API key for a provider: UI override first, then .env."""
    p = p or provider()
    return _keys.get(p, "") or _settings_key(p)


# Backwards-compatible single-provider helpers.
def anthropic_key() -> str:
    return provider_key("anthropic")


def groq_key() -> str:
    return provider_key("groq")


def model_override() -> str:
    # UI override (_ov) wins; otherwise fall back to the .env COPILOT_MODEL,
    # matching every other resolver here (the empty default lets llm.py drop
    # to the provider's built-in default).
    return _ov["model"] or get_settings().copilot_model


def fast_model_override() -> str:
    return _ov["fast_model"] or get_settings().copilot_fast_model


def set_model(provider: str, api_key: str, model: str = "", fast_model: str = "") -> None:
    prov = (provider or "").strip().lower()
    _ov["provider"] = prov
    # Store the key under the chosen provider — an empty value clears the override
    # and re-enables the .env fallback (so the UI can revert to the env key).
    if prov in _keys:
        _keys[prov] = (api_key or "").strip()
    _ov["model"] = (model or "").strip()
    _ov["fast_model"] = (fast_model or "").strip()


# ---- github ----
def set_github(token: str, repo: str) -> None:
    _ov["github_token"] = (token or "").strip()
    _ov["github_repo"] = (repo or "").strip()


def clear_github() -> None:
    _ov["github_token"] = ""
    _ov["github_repo"] = ""


def github_token() -> str:
    return _ov["github_token"] or get_settings().github_token


def github_repo() -> str:
    return _ov["github_repo"] or get_settings().github_repo


def github_connected() -> bool:
    return bool(github_token() and github_repo())


# ---- data sources ----
def repo_path() -> Path:
    return _resolve_path(_ov["repo_path"]) if _ov["repo_path"] else get_settings().repo_path


def logs_path() -> Path:
    return _resolve_path(_ov["logs_path"]) if _ov["logs_path"] else get_settings().logs_path


def set_repo_path(path: str) -> None:
    _ov["repo_path"] = (path or "").strip()


def set_logs_path(path: str) -> None:
    _ov["logs_path"] = (path or "").strip()


def model_snapshot() -> dict:
    """Capture the model/provider overrides so a failed change can be rolled back."""
    return {
        "provider": _ov["provider"],
        "model": _ov["model"],
        "fast_model": _ov["fast_model"],
        "keys": dict(_keys),
    }


def restore_model(snap: dict) -> None:
    _ov["provider"] = snap.get("provider", "")
    _ov["model"] = snap.get("model", "")
    _ov["fast_model"] = snap.get("fast_model", "")
    saved = snap.get("keys", {})
    for p in PROVIDERS:
        _keys[p] = saved.get(p, "")


def reset_model() -> None:
    """Revert only the model/provider overrides (leave github + sources intact)."""
    _ov["provider"] = ""
    _ov["model"] = ""
    _ov["fast_model"] = ""
    for p in PROVIDERS:
        _keys[p] = ""


def reset() -> None:
    """Revert every runtime override back to the .env defaults."""
    for k in _ov:
        _ov[k] = ""
    for p in PROVIDERS:
        _keys[p] = ""
