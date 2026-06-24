"""GitHub MCP server.

Provides repository-host tools. When GITHUB_TOKEN is set it talks to the real
GitHub REST API; otherwise it runs in OFFLINE DEMO mode and returns realistic
fixtures so the whole agent is runnable without any external accounts.

The `create_pull_request` tool is intentionally a *write* action — the agent
must route it through the human-in-the-loop approval node before calling it.

Tools:
  - list_recent_commits:  recent commits on a branch
  - get_commit_diff:      the patch for a specific commit
  - create_pull_request:  (WRITE) open a PR with a proposed fix
"""

from __future__ import annotations

import os

from mcp.server.fastmcp import FastMCP

GITHUB_TOKEN = os.environ.get("GITHUB_TOKEN", "")
# "owner/repo" the real-API calls target. Required only in real (token) mode.
GITHUB_REPO = os.environ.get("GITHUB_REPO", "").strip()
OFFLINE = not GITHUB_TOKEN

mcp = FastMCP("github")


def _repo_or_error() -> tuple[str | None, dict | None]:
    """Return (repo, None) when configured, else (None, error-dict). Avoids the
    old hardcoded OWNER/REPO placeholders that 404'd on every real-mode call."""
    if not GITHUB_REPO or "/" not in GITHUB_REPO:
        return None, {
            "error": "GITHUB_REPO is not set (expected 'owner/repo'). "
            "Set it to use the real GitHub API, or unset GITHUB_TOKEN for the offline demo."
        }
    return GITHUB_REPO, None


def _as_int(value, default: int) -> int:
    """Coerce a tool argument to int; LLMs often send numbers as strings."""
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


# --- Offline fixtures (used when no token is configured) -------------------
_DEMO_COMMITS = [
    {
        "sha": "abc1234",
        "author": "dana",
        "date": "2026-06-23",
        "message": "Add percentage discount support to checkout",
    },
    {
        "sha": "9f8e7d6",
        "author": "sam",
        "date": "2026-06-22",
        "message": "Refactor cart total calculation",
    },
    {
        "sha": "1122334",
        "author": "lee",
        "date": "2026-06-21",
        "message": "Bump dependencies",
    },
]

_DEMO_DIFF = """\
diff --git a/checkout.js b/checkout.js
index 8a1f..b2c3 100644
--- a/checkout.js
+++ b/checkout.js
@@ -38,7 +38,9 @@ function applyDiscount(cart, coupon) {
-  const subtotal = cart.total;
+  // BUG: `coupon` may be undefined when no coupon is supplied
+  const subtotal = cart.total;
+  const pct = coupon.total;          // <-- throws when coupon is undefined
   return subtotal - subtotal * pct;
 }
"""


@mcp.tool()
def list_recent_commits(branch: str = "main", max_count: int | str = 5) -> list[dict]:
    """List recent commits on a branch (sha, author, date, message)."""
    max_count = _as_int(max_count, 5)
    if OFFLINE:
        return _DEMO_COMMITS[:max_count]
    repo, err = _repo_or_error()
    if err:
        return [err]
    import httpx  # local import so offline mode needs no extra deps

    resp = httpx.get(
        f"https://api.github.com/repos/{repo}/commits",
        params={"sha": branch, "per_page": max_count},
        headers={"Authorization": f"Bearer {GITHUB_TOKEN}"},
        timeout=15,
    )
    resp.raise_for_status()
    return [
        {
            "sha": c["sha"][:7],
            "author": c["commit"]["author"]["name"],
            "date": c["commit"]["author"]["date"][:10],
            "message": c["commit"]["message"].splitlines()[0],
        }
        for c in resp.json()
    ]


@mcp.tool()
def get_commit_diff(sha: str) -> str:
    """Return the unified diff/patch introduced by a commit."""
    if OFFLINE:
        return _DEMO_DIFF
    repo, err = _repo_or_error()
    if err:
        return err["error"]
    import httpx

    resp = httpx.get(
        f"https://api.github.com/repos/{repo}/commits/{sha}",
        headers={
            "Authorization": f"Bearer {GITHUB_TOKEN}",
            "Accept": "application/vnd.github.v3.diff",
        },
        timeout=15,
    )
    resp.raise_for_status()
    return resp.text


@mcp.tool()
def create_pull_request(title: str, body: str, head: str, base: str = "main") -> dict:
    """(WRITE) Open a pull request proposing a fix.

    This mutates the remote — the agent must obtain human approval first.
    In offline mode it returns a simulated PR object.
    """
    if OFFLINE:
        return {
            "status": "created (simulated — offline demo mode, no real PR opened)",
            "url": "https://github.com/<owner>/<repo>/pull/42  (placeholder)",
            "title": title,
            "head": head,
            "base": base,
        }
    repo, err = _repo_or_error()
    if err:
        return err
    import httpx

    resp = httpx.post(
        f"https://api.github.com/repos/{repo}/pulls",
        json={"title": title, "body": body, "head": head, "base": base},
        headers={"Authorization": f"Bearer {GITHUB_TOKEN}"},
        timeout=15,
    )
    resp.raise_for_status()
    data = resp.json()
    return {"status": "created", "url": data["html_url"], "title": title}


if __name__ == "__main__":
    mcp.run(transport="stdio")
