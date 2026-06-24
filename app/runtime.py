"""Mutable runtime configuration set from the UI at run time.

Right now this holds GitHub credentials the user connects via the sidebar.
These override the static .env values (settings.github_token / github_repo) so a
user can switch the GitHub MCP server from offline-demo to live mode without
restarting the server. Stored in memory only — never written to disk.
"""

from __future__ import annotations

from app.config import get_settings

_github: dict[str, str] = {"token": "", "repo": ""}


def set_github(token: str, repo: str) -> None:
    _github["token"] = (token or "").strip()
    _github["repo"] = (repo or "").strip()


def clear_github() -> None:
    _github["token"] = ""
    _github["repo"] = ""


def github_token() -> str:
    """Runtime token if connected, else the .env value."""
    return _github["token"] or get_settings().github_token


def github_repo() -> str:
    return _github["repo"] or get_settings().github_repo


def github_connected() -> bool:
    """True when a usable token+repo is configured (runtime or env)."""
    return bool(github_token() and github_repo())
