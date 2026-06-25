"""Repo MCP server path sandbox — must reject traversal outside REPO_PATH."""

import os

import pytest

from app.mcp.servers.repo import server as repo


def test_safe_path_allows_inside_repo():
    p = repo._safe_path("subdir/file.txt")
    root = os.path.realpath(repo.REPO_PATH)
    assert str(p).startswith(root)


def test_safe_path_rejects_parent_traversal():
    with pytest.raises(ValueError):
        repo._safe_path("../../../etc/passwd")


def test_safe_path_rejects_absolute_escape():
    with pytest.raises(ValueError):
        repo._safe_path("/etc/passwd")
