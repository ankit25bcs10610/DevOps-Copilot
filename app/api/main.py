"""FastAPI surface for DevOps Copilot.

Endpoints:
    POST /chat     -> start an investigation; returns final answer or an
                      approval request (with the thread_id to resume).
    POST /approve  -> resume a paused investigation with a human decision.
    GET  /healthz  -> liveness probe.

Sessions are keyed by thread_id and kept alive in-process for the demo. A
production deployment would externalize this (the checkpointer already persists
graph state to SQLite/Postgres, so sessions are reconstructable).
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from app import runtime
from app.config import ROOT, get_settings
from app.llm import active_api_key, resolved_models
from app.session import CopilotSession, TurnResult

# Static catalog of the MCP servers + their tools, surfaced to the UI sidebar.
# Mirrors app/mcp/client.py — kept here so /config needs no server subprocesses.
MCP_CATALOG = [
    {
        "name": "logs-metrics",
        "label": "Logs & Metrics",
        "custom": True,
        "tools": ["search_logs", "get_error_summary", "get_metric", "list_services"],
    },
    {
        "name": "repo",
        "label": "Repository",
        "custom": True,
        "tools": ["list_dir", "read_file", "grep", "git_log"],
    },
    {
        "name": "github",
        "label": "GitHub",
        "custom": True,
        "tools": ["list_recent_commits", "get_commit_diff", "create_pull_request"],
    },
]

# thread_id -> live session
_SESSIONS: dict[str, CopilotSession] = {}
# Serializes session creation so concurrent first requests for the same thread
# don't each spin up (and leak) a session.
_SESSION_LOCK = asyncio.Lock()


async def _evict(thread_id: str) -> None:
    """Drop a session and release its MCP subprocesses, ignoring teardown errors."""
    session = _SESSIONS.pop(thread_id, None)
    if session is not None:
        try:
            await session.__aexit__(None, None, None)
        except Exception:  # noqa: BLE001 — best-effort cleanup
            pass


async def _evict_all() -> None:
    """Drop every session so future ones rebuild MCP with current credentials."""
    for thread_id in list(_SESSIONS):
        await _evict(thread_id)


def _github_status() -> dict:
    return {
        "connected": runtime.github_connected(),
        "repo": runtime.github_repo() or None,
        "mode": "live" if runtime.github_connected() else "offline",
    }


@asynccontextmanager
async def lifespan(app: FastAPI):
    yield
    # Clean up any sessions still open at shutdown.
    for session in list(_SESSIONS.values()):
        await session.__aexit__(None, None, None)
    _SESSIONS.clear()


app = FastAPI(title="DevOps Copilot", version="0.1.0", lifespan=lifespan)

# Allow the React dev server plus any origins configured via CORS_ORIGINS.
app.add_middleware(
    CORSMiddleware,
    allow_origins=get_settings().allowed_origins,
    allow_methods=["*"],
    allow_headers=["*"],
)


class ChatRequest(BaseModel):
    thread_id: str
    message: str


class ApproveRequest(BaseModel):
    thread_id: str
    approved: bool
    reason: str = ""


class GithubConnectRequest(BaseModel):
    token: str
    repo: str  # "owner/repo"


class ModelConfigRequest(BaseModel):
    provider: str  # "anthropic" | "groq"
    api_key: str = ""
    model: str = ""
    fast_model: str = ""


class SourcePathRequest(BaseModel):
    path: str


class ChatResponse(BaseModel):
    thread_id: str
    status: str  # "completed" | "awaiting_approval" | "error"
    answer: str = ""
    approval_request: dict | None = None
    trace: list[str] = []


def _friendly_error(exc: Exception) -> str:
    """Turn a raw exception into a message safe to show in the UI."""
    text = str(exc)
    if "rate_limit" in text or "429" in text:
        return (
            "The LLM provider's rate limit was reached. Please try again later "
            "or check your plan/quota."
        )
    if "authentication" in text.lower() or "401" in text or "api_key" in text.lower():
        return (
            "The LLM provider rejected the API key. Check the key for the "
            "configured COPILOT_PROVIDER in your .env."
        )
    return f"Something went wrong while running the agent: {text}"


def _to_response(thread_id: str, result: TurnResult) -> ChatResponse:
    return ChatResponse(
        thread_id=thread_id,
        status=result.status,
        answer=result.final_text,
        approval_request=result.approval_request,
        trace=result.trace,
    )


async def _get_session(thread_id: str, create: bool) -> CopilotSession:
    session = _SESSIONS.get(thread_id)
    if session is not None:
        return session
    if not create:
        raise HTTPException(404, f"no active session for thread '{thread_id}'")
    # Re-check under the lock so two concurrent first-requests don't both build one.
    async with _SESSION_LOCK:
        session = _SESSIONS.get(thread_id)
        if session is None:
            session = await CopilotSession(thread_id=thread_id).__aenter__()
            _SESSIONS[thread_id] = session
        return session


@app.get("/healthz")
async def healthz() -> dict:
    return {"status": "ok", "active_sessions": len(_SESSIONS)}


@app.get("/config")
async def config() -> dict:
    """Describe the running agent (provider, models, MCP servers) for the UI."""
    main_model, fast_model = resolved_models()
    return {
        "provider": runtime.provider(),
        "model": main_model,
        "fast_model": fast_model,
        "offline_mode": not runtime.github_connected(),
        "servers": MCP_CATALOG,
        "github": _github_status(),
        "sources": {
            "repo_path": str(runtime.repo_path()),
            "logs_path": str(runtime.logs_path()),
        },
        "has_key": bool(active_api_key()),
    }


@app.get("/github/status")
async def github_status() -> dict:
    return _github_status()


@app.post("/github/connect")
async def github_connect(req: GithubConnectRequest) -> dict:
    """Validate a GitHub token + repo against the real API, then switch the
    GitHub MCP server into live mode (in-memory only — not persisted)."""
    token = req.token.strip()
    repo = req.repo.strip()
    if not token or "/" not in repo:
        raise HTTPException(400, "Provide a token and a repo as 'owner/repo'.")

    # Verify the credentials actually work before storing them.
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(
                f"https://api.github.com/repos/{repo}",
                headers={
                    "Authorization": f"Bearer {token}",
                    "Accept": "application/vnd.github+json",
                },
            )
    except httpx.HTTPError as exc:
        raise HTTPException(502, f"Could not reach GitHub: {exc}") from exc

    if resp.status_code == 401:
        raise HTTPException(401, "GitHub rejected the token (check it's valid).")
    if resp.status_code == 404:
        raise HTTPException(404, f"Repo '{repo}' not found or the token lacks access.")
    if resp.status_code >= 400:
        raise HTTPException(resp.status_code, f"GitHub error: {resp.text[:200]}")

    runtime.set_github(token, repo)
    await _evict_all()  # rebuild sessions so the MCP server picks up live creds
    data = resp.json()
    status = _github_status()
    status["full_name"] = data.get("full_name", repo)
    status["private"] = data.get("private")
    return status


@app.post("/github/disconnect")
async def github_disconnect() -> dict:
    runtime.clear_github()
    await _evict_all()
    return _github_status()


@app.post("/model/configure")
async def model_configure(req: ModelConfigRequest) -> dict:
    """Switch the LLM provider/model (and key) at runtime. Pasting an Anthropic
    key here unlocks Claude Opus 4.8 without touching .env."""
    provider = req.provider.strip().lower()
    if provider not in {"anthropic", "groq"}:
        raise HTTPException(400, "provider must be 'anthropic' or 'groq'")

    runtime.set_model(provider, req.api_key, req.model, req.fast_model)
    if not active_api_key():
        # Revert ONLY the model fields — keep github/source overrides intact.
        runtime.reset_model()
        raise HTTPException(400, f"An API key is required for '{provider}'.")

    await _evict_all()  # rebuild sessions so the new model/key is used
    main_model, fast_model = resolved_models()
    return {"provider": runtime.provider(), "model": main_model, "fast_model": fast_model}


def _set_source(path: str, kind: str, setter) -> dict:
    p = Path(path).expanduser()
    if not p.is_absolute():
        p = (ROOT / p)
    if not p.exists():
        raise HTTPException(404, f"path does not exist: {p}")
    if not p.is_dir():
        raise HTTPException(400, f"path is not a directory: {p}")
    setter(path.strip())
    return str(p.resolve())


@app.post("/sources/repo")
async def set_repo_source(req: SourcePathRequest) -> dict:
    resolved = _set_source(req.path, "repo", runtime.set_repo_path)
    await _evict_all()
    return {"repo_path": resolved}


@app.post("/sources/logs")
async def set_logs_source(req: SourcePathRequest) -> dict:
    resolved = _set_source(req.path, "logs", runtime.set_logs_path)
    # Soft warning if the expected demo files aren't present.
    p = Path(resolved)
    missing = [f for f in ("app.log", "metrics.json") if not (p / f).exists()]
    await _evict_all()
    return {"logs_path": resolved, "missing_files": missing}


@app.post("/reset")
async def reset_config() -> dict:
    """Revert all runtime overrides back to the .env defaults."""
    runtime.reset()
    await _evict_all()
    main_model, fast_model = resolved_models()
    return {
        "provider": runtime.provider(),
        "model": main_model,
        "fast_model": fast_model,
        "github": _github_status(),
        "sources": {
            "repo_path": str(runtime.repo_path()),
            "logs_path": str(runtime.logs_path()),
        },
    }


@app.post("/chat", response_model=ChatResponse)
async def chat(req: ChatRequest) -> ChatResponse:
    existed = req.thread_id in _SESSIONS
    session = await _get_session(req.thread_id, create=True)
    try:
        result = await session.ask(req.message)
    except Exception as exc:  # noqa: BLE001 — surface a clean error to the UI
        # If this session was just created and its first turn failed, drop it so
        # a retry rebuilds cleanly instead of reusing a half-initialized session.
        if not existed:
            await _evict(req.thread_id)
        return ChatResponse(
            thread_id=req.thread_id, status="error", answer=_friendly_error(exc)
        )
    return _to_response(req.thread_id, result)


@app.post("/approve", response_model=ChatResponse)
async def approve(req: ApproveRequest) -> ChatResponse:
    session = await _get_session(req.thread_id, create=False)
    try:
        result = await session.resume(approved=req.approved, reason=req.reason)
    except Exception as exc:  # noqa: BLE001
        return ChatResponse(
            thread_id=req.thread_id, status="error", answer=_friendly_error(exc)
        )
    return _to_response(req.thread_id, result)
