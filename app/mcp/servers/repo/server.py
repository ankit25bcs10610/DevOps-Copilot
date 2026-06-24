"""Repository MCP server — read-only access to a target code repo.

Exposes safe, sandboxed filesystem + git tools. Every path is resolved and
checked to stay inside REPO_PATH, so the agent can never read outside the repo.

Tools:
  - list_dir:  list files/dirs under a relative path
  - read_file: read a file (with optional line range)
  - grep:      search the repo for a pattern, return file:line:text matches
  - git_log:   recent commit history (uses `git` if the repo is a git repo)

Run standalone:
    python -m app.mcp.servers.repo.server
"""

from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path

from mcp.server.fastmcp import FastMCP

REPO_PATH = Path(os.environ.get("TARGET_REPO_PATH", "./sample_repo")).resolve()

mcp = FastMCP("repo")

# Directories we never descend into.
_IGNORE = {".git", "__pycache__", "node_modules", ".venv", "venv"}


def _as_int(value, default: int) -> int:
    """Coerce a tool argument to int; LLMs often send numbers as strings."""
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _safe_path(relative: str) -> Path:
    """Resolve a relative path and ensure it stays inside REPO_PATH.

    Uses realpath so symlinks are fully resolved before the boundary check —
    a symlinked child that points outside the repo is rejected.
    """
    root = Path(os.path.realpath(REPO_PATH))
    candidate = Path(os.path.realpath(REPO_PATH / relative))
    if candidate != root:
        try:
            candidate.relative_to(root)
        except ValueError:
            raise ValueError(f"path '{relative}' escapes the repository sandbox")
    return candidate


@mcp.tool()
def list_dir(path: str = ".") -> list[str]:
    """List files and directories under a path, relative to the repo root."""
    target = _safe_path(path)
    if not target.exists():
        return [f"error: '{path}' does not exist"]
    entries = []
    for child in sorted(target.iterdir()):
        if child.name in _IGNORE:
            continue
        rel = child.relative_to(REPO_PATH)
        entries.append(f"{rel}/" if child.is_dir() else str(rel))
    return entries


@mcp.tool()
def read_file(path: str, start_line: int | str = 1, end_line: int | str | None = None) -> str:
    """Read a file from the repo, optionally restricted to a line range.

    Lines are 1-indexed and the range is inclusive. Output is prefixed with
    line numbers so the agent can reference exact locations.
    """
    target = _safe_path(path)
    if not target.is_file():
        return f"error: '{path}' is not a file"
    # Clamp to >=1 so start_line=0 doesn't become lines[-1:] (negative-index wrap).
    start_line = max(1, _as_int(start_line, 1))
    lines = target.read_text(errors="replace").splitlines()
    end = _as_int(end_line, len(lines)) if end_line is not None else len(lines)
    selected = lines[start_line - 1 : end]
    return "\n".join(f"{start_line + i:>4}\t{ln}" for i, ln in enumerate(selected))


@mcp.tool()
def grep(pattern: str, glob: str = "*", max_results: int | str = 50) -> list[str]:
    """Search the repo for a regex pattern.

    Returns matches as "relative/path:line: matched text". `glob` restricts the
    filename pattern (e.g. "*.js", "*.py").
    """
    max_results = _as_int(max_results, 50)
    try:
        regex = re.compile(pattern)
    except re.error as exc:
        return [f"error: invalid regex: {exc}"]

    results: list[str] = []
    for file in REPO_PATH.rglob(glob):
        if not file.is_file() or file.is_symlink():
            continue
        # Ignore based on path *relative to the repo* — otherwise an ancestor
        # directory named e.g. "venv" above REPO_PATH would skip the whole repo.
        rel = file.relative_to(REPO_PATH)
        if any(part in _IGNORE for part in rel.parts):
            continue
        try:
            for n, line in enumerate(file.read_text(errors="replace").splitlines(), 1):
                if regex.search(line):
                    results.append(f"{rel}:{n}: {line.strip()}")
                    if len(results) >= max_results:
                        return results
        except (UnicodeDecodeError, OSError):
            continue
    return results or ["(no matches)"]


@mcp.tool()
def git_log(max_count: int | str = 5) -> list[str]:
    """Return recent git commits as 'hash | author | date | subject'.

    Falls back gracefully if the target is not a git repository.
    """
    max_count = _as_int(max_count, 5)
    try:
        out = subprocess.run(
            ["git", "-C", str(REPO_PATH), "log", f"-{max_count}",
             "--pretty=format:%h | %an | %ad | %s", "--date=short"],
            capture_output=True, text=True, timeout=10,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
        return [f"error: {exc}"]
    if out.returncode != 0:
        return [f"error: {out.stderr.strip() or 'not a git repository'}"]
    return out.stdout.splitlines()


if __name__ == "__main__":
    mcp.run(transport="stdio")
