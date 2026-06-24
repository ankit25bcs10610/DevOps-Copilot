"""Mutable runtime configuration set from the UI at run time.

Holds overrides for the model/provider, GitHub credentials, and the repo/logs
data sources. These take precedence over the static .env values (Settings) so a
user can reconfigure the agent from the sidebar without restarting the server.
Stored in memory only — never written to disk.
"""

from __future__ import annotations

from pathlib import Path

from app.config import ROOT, get_settings

_ov: dict[str, str] = {
    # model / provider
    "provider": "",
    "anthropic_key": "",
    "groq_key": "",
    "model": "",
    "fast_model": "",
    # github
    "github_token": "",
    "github_repo": "",
    # data sources
    "repo_path": "",
    "logs_path": "",
}


def _resolve_path(p: str) -> Path:
    pp = Path(p).expanduser()
    return pp if pp.is_absolute() else (ROOT / pp)


# ---- model / provider ----
def provider() -> str:
    return (_ov["provider"] or get_settings().copilot_provider).lower()


def anthropic_key() -> str:
    return _ov["anthropic_key"] or get_settings().anthropic_api_key


def groq_key() -> str:
    return _ov["groq_key"] or get_settings().groq_api_key


def model_override() -> str:
    return _ov["model"]


def fast_model_override() -> str:
    return _ov["fast_model"]


def set_model(provider: str, api_key: str, model: str = "", fast_model: str = "") -> None:
    prov = (provider or "").strip().lower()
    _ov["provider"] = prov
    # Always write the chosen provider's key — an empty value clears the override
    # and re-enables the .env fallback (so the UI can revert to the env key).
    if prov == "anthropic":
        _ov["anthropic_key"] = (api_key or "").strip()
    elif prov == "groq":
        _ov["groq_key"] = (api_key or "").strip()
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


_MODEL_KEYS = ("provider", "anthropic_key", "groq_key", "model", "fast_model")


def model_snapshot() -> dict[str, str]:
    """Capture the model/provider overrides so a failed change can be rolled back."""
    return {k: _ov[k] for k in _MODEL_KEYS}


def restore_model(snap: dict[str, str]) -> None:
    for k in _MODEL_KEYS:
        _ov[k] = snap.get(k, "")


def reset_model() -> None:
    """Revert only the model/provider overrides (leave github + sources intact)."""
    for k in _MODEL_KEYS:
        _ov[k] = ""


def reset() -> None:
    """Revert every runtime override back to the .env defaults."""
    for k in _ov:
        _ov[k] = ""
