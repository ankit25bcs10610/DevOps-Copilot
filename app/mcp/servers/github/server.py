"""GitHub MCP server.

Provides repository-host tools. When GITHUB_TOKEN is set it talks to the real
GitHub REST API; otherwise it runs in OFFLINE DEMO mode and returns realistic
fixtures so the whole agent is runnable without any external accounts.

The `create_pull_request` tool is intentionally a *write* action — the agent
must route it through the human-in-the-loop approval node before calling it.

Tools:
  - list_recent_commits:  recent commits on a branch
  - get_commit_diff:      the patch for a specific commit
  - list_workflow_runs:   recent GitHub Actions CI runs (status/conclusion)
  - get_failed_job_logs:  logs of the failed job(s) in a run ("why did the deploy fail")
  - create_pull_request:  (WRITE) open a PR with a proposed fix
"""

from __future__ import annotations

import os
import re

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


_DEMO_WORKFLOW_RUNS = [
    {
        "id": 980001, "name": "CI", "event": "push", "branch": "main",
        "status": "completed", "conclusion": "failure", "head_sha": "abc1234",
        "created_at": "2026-06-23T09:50:00Z",
        "html_url": "https://github.com/<owner>/<repo>/actions/runs/980001",
        "title": "Add percentage discount support to checkout",
    },
    {
        "id": 979544, "name": "CI", "event": "push", "branch": "main",
        "status": "completed", "conclusion": "success", "head_sha": "9f8e7d6",
        "created_at": "2026-06-22T16:10:00Z",
        "html_url": "https://github.com/<owner>/<repo>/actions/runs/979544",
        "title": "Refactor cart total calculation",
    },
]

_DEMO_FAILED_JOB_LOG = """\
> checkout-svc@1.8.0 test
> jest

 FAIL  test/checkout.test.js
  ● applyDiscount › applies no discount when coupon is omitted

    TypeError: Cannot read properties of undefined (reading 'total')

      40 | function applyDiscount(cart, coupon) {
      41 |   const subtotal = cart.total;
    > 42 |   const pct = coupon.total;
         |                      ^
      43 |   return subtotal - subtotal * pct;

      at applyDiscount (checkout.js:42:22)

Tests: 1 failed, 7 passed, 8 total
Error: Process completed with exit code 1.
"""


# --- "What changed" correlation -------------------------------------------- #
# ~80% of incidents trace to a recent change, so ranking recent commits by their
# textual overlap with the incident signal (failing symbol / file / keywords) is
# one of the highest-ROI moves. Scoring is a pure helper so it's unit-testable.
_STOP = {
    "add", "the", "to", "for", "of", "and", "fix", "fixes", "update", "updates",
    "support", "refactor", "bump", "in", "on", "a", "an", "with", "from", "into",
}


def _tokenize(text: str) -> set[str]:
    return {t for t in re.findall(r"[a-zA-Z][a-zA-Z0-9_]{2,}", (text or "").lower())} - _STOP


def _overlap(signal_tokens: set[str], message: str) -> list[str]:
    """Tokens shared between the incident signal and a commit message, counting a
    containment match (e.g. 'applyDiscount' ↔ 'discount') so symbol names hit."""
    msg_tokens = _tokenize(message)
    matched: set[str] = set()
    for s in signal_tokens:
        for m in msg_tokens:
            if s == m or (len(s) >= 4 and len(m) >= 4 and (s in m or m in s)):
                matched.add(m)
    return sorted(matched)


def _rank_commits(signal: str, commits: list[dict]) -> list[dict]:
    """Score commits (newest-first) by overlap with the signal; return ranked,
    stable-sorted so recency breaks ties."""
    sig = _tokenize(signal)
    scored = []
    for i, c in enumerate(commits):
        matched = _overlap(sig, c.get("message", ""))
        scored.append({**c, "score": len(matched), "matched": matched, "_i": i})
    scored.sort(key=lambda c: (-c["score"], c["_i"]))
    for c in scored:
        c.pop("_i", None)
    return scored


def _first_bad_deploy(commits: list[dict], onset_date: str) -> dict:
    """Given commits (each with a 'date' YYYY-MM-DD) and an incident onset date,
    return the most recent commit dated on/before onset (the suspect deploy) plus
    the commits that landed after it. Pure + testable. Turns text-overlap guessing
    into a time-anchored claim: the last change before the error began."""
    onset_day = (onset_date or "")[:10]
    dated = [c for c in commits if c.get("date")]
    # Newest first; the first commit at/before onset is the last deploy before the incident.
    dated.sort(key=lambda c: c["date"], reverse=True)
    after = [c for c in dated if c["date"] > onset_day]
    suspect = next((c for c in dated if c["date"] <= onset_day), None)
    return {
        "onset_date": onset_day,
        "suspect": suspect,
        "why": (
            f"Commit {suspect['sha']} ({suspect['date']}) is the last change at or "
            f"before the incident onset — the prime suspect." if suspect else
            "No commit dated at/before the onset; the cause may predate the history window."
        ),
        "landed_after_onset": after,
    }


@mcp.tool()
def first_bad_deploy(onset_timestamp: str, branch: str = "main") -> dict:
    """Time-anchored deploy bisect: given when the incident began, identify the last
    deploy before onset (the prime suspect) — a causal claim, not a keyword guess.
    Pair with `correlate_changes` (content match) for a stronger verdict."""
    commits = list_recent_commits(branch=branch, max_count=20)
    if commits and isinstance(commits[0], dict) and commits[0].get("error"):
        return {"error": commits[0]["error"]}
    return _first_bad_deploy(commits, onset_timestamp)


@mcp.tool()
def correlate_changes(signal: str, branch: str = "main", max_results: int | str = 5) -> dict:
    """Rank recent commits by how strongly they relate to an incident signal — the
    'what changed' step. Pass the failing symbol/file/keywords (e.g.
    "applyDiscount checkout.js discount 500") and get the most suspect commits
    plus the top one's diff.

    Returns {"suspects": [{sha, date, message, score, matched}], "top_diff": "..."}.
    """
    max_results = _as_int(max_results, 5)
    commits = list_recent_commits(branch=branch, max_count=20)
    # list_recent_commits returns an error-dict list in some failure modes.
    if commits and isinstance(commits[0], dict) and commits[0].get("error"):
        return {"error": commits[0]["error"]}
    ranked = _rank_commits(signal, commits)[:max_results]
    top_diff = ""
    if ranked and ranked[0]["score"] > 0:
        top_diff = get_commit_diff(ranked[0]["sha"])
    return {"signal": signal, "suspects": ranked, "top_diff": top_diff}


@mcp.tool()
def list_workflow_runs(branch: str = "main", max_count: int | str = 10) -> list[dict]:
    """List recent GitHub Actions runs (id, name, status, conclusion, head_sha) —
    the 'did the last deploy/CI pass?' signal to correlate with an incident's onset."""
    max_count = _as_int(max_count, 10)
    if OFFLINE:
        return _DEMO_WORKFLOW_RUNS[:max_count]
    repo, err = _repo_or_error()
    if err:
        return [err]
    import httpx

    resp = httpx.get(
        f"https://api.github.com/repos/{repo}/actions/runs",
        params={"branch": branch, "per_page": max_count},
        headers={"Authorization": f"Bearer {GITHUB_TOKEN}"},
        timeout=15,
    )
    resp.raise_for_status()
    return [
        {
            "id": r["id"], "name": r.get("name"), "event": r.get("event"),
            "branch": r.get("head_branch"), "status": r.get("status"),
            "conclusion": r.get("conclusion"), "head_sha": (r.get("head_sha") or "")[:7],
            "created_at": r.get("created_at"), "html_url": r.get("html_url"),
        }
        for r in resp.json().get("workflow_runs", [])
    ]


@mcp.tool()
def get_failed_job_logs(run_id: int | str, max_chars: int | str = 4000) -> dict:
    """Return the log tail of the FAILED job(s) in a workflow run — answers
    'why did the deploy/CI fail' and surfaces the failing test/stack trace."""
    max_chars = _as_int(max_chars, 4000)
    if OFFLINE:
        return {
            "run_id": _as_int(run_id, 0),
            "failed_jobs": [{"name": "test", "conclusion": "failure",
                             "log_tail": _DEMO_FAILED_JOB_LOG[:max_chars]}],
        }
    repo, err = _repo_or_error()
    if err:
        return err
    import httpx

    headers = {"Authorization": f"Bearer {GITHUB_TOKEN}"}
    jobs = httpx.get(
        f"https://api.github.com/repos/{repo}/actions/runs/{run_id}/jobs",
        headers=headers, timeout=15,
    )
    jobs.raise_for_status()
    failed = []
    for job in jobs.json().get("jobs", []):
        if job.get("conclusion") == "failure":
            log = httpx.get(
                f"https://api.github.com/repos/{repo}/actions/jobs/{job['id']}/logs",
                headers=headers, timeout=20, follow_redirects=True,
            )
            tail = log.text[-max_chars:] if log.status_code < 400 else f"(could not fetch logs: {log.status_code})"
            failed.append({"name": job.get("name"), "conclusion": "failure", "log_tail": tail})
    return {"run_id": _as_int(run_id, 0), "failed_jobs": failed or [{"note": "no failed jobs in this run"}]}


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
