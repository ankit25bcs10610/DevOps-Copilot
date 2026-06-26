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
import hmac
import json
import logging
import time
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import parse_qs

import httpx
from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from sse_starlette.sse import EventSourceResponse
from starlette.middleware.base import BaseHTTPMiddleware

from app import audit, feedback, metrics_source, observability, runtime
from app.config import ROOT, get_settings
from app.integrations import pagerduty as pd_webhook
from app.integrations import slack
from app.llm import active_api_key, resolved_models
from app.session import CopilotSession, TurnResult

# Activate logging + LangSmith tracing before any model/graph is built.
observability.init()
log = logging.getLogger("devcopilot.api")

# Static catalog of the MCP servers + their tools, surfaced to the UI sidebar.
# Mirrors app/mcp/client.py — kept here so /config needs no server subprocesses.
MCP_CATALOG = [
    {
        "name": "datadog",
        "label": "Datadog",
        "custom": True,
        "tools": [
            "search_logs", "get_error_summary", "get_metric", "list_services",
            "detect_anomaly", "compute_burn_rate", "onset_timeline",
        ],
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
        "tools": [
            "list_recent_commits", "get_commit_diff", "correlate_changes", "first_bad_deploy",
            "list_workflow_runs", "get_failed_job_logs", "create_pull_request",
        ],
    },
    {
        "name": "pagerduty",
        "label": "PagerDuty",
        "custom": True,
        "tools": [
            "list_incidents", "get_incident", "get_incident_alerts",
            "add_incident_note", "acknowledge_incident", "resolve_incident",
        ],
    },
    {
        "name": "kubernetes",
        "label": "Kubernetes",
        "custom": True,
        "tools": [
            "list_pods", "describe_pod", "get_events", "get_deployment_status",
            "rollout_history", "scale_deployment", "rollback_deployment", "restart_deployment",
        ],
    },
    {
        "name": "sentry",
        "label": "Sentry",
        "custom": True,
        "tools": ["list_issues", "get_issue", "get_latest_event"],
    },
    {
        "name": "memory",
        "label": "Incident memory",
        "custom": True,
        "tools": ["search_incidents", "get_incident_record"],
    },
    {
        "name": "traces",
        "label": "Traces",
        "custom": True,
        "tools": [
            "search_traces", "get_trace", "service_dependencies", "analyze_blast_radius",
            "analyze_critical_path", "get_exemplars",
        ],
    },
]

# thread_id -> live session (insertion-ordered; used for LRU eviction).
_SESSIONS: dict[str, CopilotSession] = {}
# Serializes session creation/reconstruction so concurrent first-requests for the
# same thread don't each spin up (and leak) a session.
_SESSION_LOCK = asyncio.Lock()
# Per-thread locks: one thread can't run two turns at once, WITHOUT serializing
# unrelated threads (so different users investigate in parallel).
_THREAD_LOCKS: dict[str, asyncio.Lock] = {}
# Liveness bookkeeping for safe eviction: threads with a turn in flight, threads
# paused awaiting approval, and last-use timestamps (for LRU).
_RUNNING: set[str] = set()
_AWAITING: set[str] = set()
_LAST_USED: dict[str, float] = {}


def _thread_lock(thread_id: str) -> asyncio.Lock:
    lock = _THREAD_LOCKS.get(thread_id)
    if lock is None:
        lock = asyncio.Lock()
        _THREAD_LOCKS[thread_id] = lock
    return lock


class _ConfigGate:
    """A writer-preferring reader/writer gate.

    Agent turns are *readers* — many run concurrently across threads. A config
    change (model / sources / github / reset / shutdown) is the *writer* — it
    waits for in-flight turns to drain, then runs exclusively so it can never
    tear a session down mid-investigation. This replaces the old single global
    lock that forced every investigation in the whole process to run one-at-a-time.
    """

    def __init__(self) -> None:
        self._cond = asyncio.Condition()
        self._readers = 0
        self._writer = False
        self._writers_waiting = 0

    @asynccontextmanager
    async def turn(self):
        async with self._cond:
            while self._writer or self._writers_waiting:
                await self._cond.wait()
            self._readers += 1
        try:
            yield
        finally:
            async with self._cond:
                self._readers -= 1
                if self._readers == 0:
                    self._cond.notify_all()

    @asynccontextmanager
    async def exclusive(self):
        async with self._cond:
            self._writers_waiting += 1
            try:
                while self._writer or self._readers > 0:
                    await self._cond.wait()
                self._writer = True
            finally:
                self._writers_waiting -= 1
        try:
            yield
        finally:
            async with self._cond:
                self._writer = False
                self._cond.notify_all()


_gate = _ConfigGate()

# Paths that bypass auth AND the limits middleware (probes + CORS preflight).
_OPEN_PATHS = frozenset({"/healthz", "/readyz"})

# --- In-memory per-IP fixed-window rate limiter (single-instance only) ---
# {ip: (window_start_epoch, count)}. Good enough for one process; a multi-instance
# deployment should front this with a shared limiter (e.g. Redis / a gateway).
_RL: dict[str, tuple[float, int]] = {}
_RL_LOCK = asyncio.Lock()
_RL_MAX_KEYS = 10_000  # prune entries from old windows past this, to bound memory


def _client_ip(request: Request, trust_proxy: bool) -> str:
    # Only trust X-Forwarded-For behind a known proxy — otherwise any client can
    # spoof it to dodge the per-IP limiter.
    if trust_proxy:
        fwd = request.headers.get("x-forwarded-for")
        if fwd:
            return fwd.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


async def _rate_limited(ip: str, limit_per_min: int) -> bool:
    now = time.time()
    async with _RL_LOCK:
        if len(_RL) > _RL_MAX_KEYS:  # bound memory: drop entries from elapsed windows
            for k in [k for k, (start, _) in _RL.items() if now - start >= 60]:
                del _RL[k]
        start, count = _RL.get(ip, (now, 0))
        if now - start >= 60:  # window rolled over
            start, count = now, 0
        count += 1
        _RL[ip] = (start, count)
        return count > limit_per_min


class LimitsMiddleware(BaseHTTPMiddleware):
    """Request-id propagation + body-size cap + per-IP rate limit (POSTs). Added
    inside the CORS middleware so 413/429 responses still carry CORS headers."""

    async def dispatch(self, request: Request, call_next):
        rid = request.headers.get("x-request-id") or uuid.uuid4().hex[:12]
        token = observability.request_id_var.set(rid)
        try:
            response = await self._guarded(request, call_next)
            response.headers["x-request-id"] = rid
            return response
        finally:
            observability.request_id_var.reset(token)

    async def _guarded(self, request: Request, call_next):
        if request.method == "OPTIONS" or request.url.path in _OPEN_PATHS:
            return await call_next(request)
        s = get_settings()
        cl = request.headers.get("content-length")
        if cl and cl.isdigit() and int(cl) > s.copilot_max_body_bytes:
            return JSONResponse({"detail": "Request body too large."}, status_code=413)
        if request.method == "POST" and s.copilot_rate_limit_per_min > 0:
            ip = _client_ip(request, s.copilot_trust_proxy)
            if await _rate_limited(ip, s.copilot_rate_limit_per_min):
                return JSONResponse(
                    {"detail": "Rate limit exceeded — slow down and retry."},
                    status_code=429,
                    headers={"Retry-After": "60"},
                )
        return await call_next(request)


async def _evict(thread_id: str) -> None:
    """Drop a session and release its MCP subprocesses, ignoring teardown errors."""
    session = _SESSIONS.pop(thread_id, None)
    _AWAITING.discard(thread_id)
    _LAST_USED.pop(thread_id, None)
    _THREAD_LOCKS.pop(thread_id, None)
    if session is not None:
        try:
            await session.__aexit__(None, None, None)
        except Exception:  # noqa: BLE001 — best-effort cleanup
            pass


async def _evict_all() -> None:
    """Drop every session so future ones rebuild MCP with current credentials.
    Runs as the config gate's exclusive writer, so it drains in-flight turns
    first and never tears a session down mid-investigation."""
    async with _gate.exclusive():
        for thread_id in list(_SESSIONS):
            await _evict(thread_id)


@asynccontextmanager
async def _running_turn(thread_id: str):
    """Scope one agent turn: concurrent across threads (config-gate reader) but
    serialized per-thread, with liveness bookkeeping so eviction never drops a
    session that's mid-turn."""
    async with _gate.turn():
        async with _thread_lock(thread_id):
            _RUNNING.add(thread_id)
            try:
                yield
            finally:
                _RUNNING.discard(thread_id)
                _LAST_USED[thread_id] = time.monotonic()


def _github_status() -> dict:
    return {
        "connected": runtime.github_connected(),
        "repo": runtime.github_repo() or None,
        "mode": "live" if runtime.github_connected() else "offline",
    }


async def _drain_and_close() -> None:
    """Close every open session, releasing its MCP subprocesses."""
    for session in list(_SESSIONS.values()):
        try:
            await session.__aexit__(None, None, None)
        except Exception:  # noqa: BLE001 — best-effort shutdown
            pass
    _SESSIONS.clear()


@asynccontextmanager
async def lifespan(app: FastAPI):
    yield
    # Graceful shutdown: the config gate's exclusive writer waits for in-flight
    # turns to drain before tearing sessions down, so we don't kill MCP
    # subprocesses mid-investigation. Bounded so a stuck turn can't hang shutdown.
    try:
        async with asyncio.timeout(30):
            async with _gate.exclusive():
                await _drain_and_close()
    except (TimeoutError, asyncio.TimeoutError):
        log.warning("shutdown drain timed out after 30s; forcing session cleanup")
        await _drain_and_close()


async def require_auth(request: Request) -> None:
    """Bearer-token gate for every route except probes and CORS preflight.

    When COPILOT_API_TOKEN is unset the API is open (local-dev default); set it
    in production and the frontend must send `Authorization: Bearer <token>`.
    (The static SPA mount is a sub-app, so it bypasses this dependency entirely.)
    """
    if request.method == "OPTIONS" or request.url.path in _OPEN_PATHS:
        return
    # Webhooks authenticate via their own provider signatures (HMAC), not the
    # shared bearer token — PagerDuty/Slack don't send it.
    if request.url.path.startswith("/webhooks/"):
        return
    expected = get_settings().copilot_api_token
    if not expected:
        return  # auth disabled (dev)
    provided = request.headers.get("authorization", "")
    # Constant-time compare so the token can't be recovered via response timing.
    if not hmac.compare_digest(provided, f"Bearer {expected}"):
        raise HTTPException(401, "Missing or invalid API token.")


app = FastAPI(
    title="DevOps Copilot",
    version="0.1.0",
    lifespan=lifespan,
    dependencies=[Depends(require_auth)],
)

# Body-size + rate-limit guard. Added FIRST so it ends up INSIDE the CORS
# middleware (add_middleware is LIFO) — that way a 413/429 still gets CORS
# headers and the browser can read it.
app.add_middleware(LimitsMiddleware)

# Allow the React dev server plus any origins configured via CORS_ORIGINS.
app.add_middleware(
    CORSMiddleware,
    allow_origins=get_settings().allowed_origins,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type"],
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
    provider: str  # anthropic | openai | gemini | groq | deepseek
    api_key: str = ""
    model: str = ""
    fast_model: str = ""


class SourcePathRequest(BaseModel):
    path: str


class FeedbackRequest(BaseModel):
    thread_id: str
    rating: str  # "up" | "down"
    comment: str = ""
    question: str = ""


class ChatResponse(BaseModel):
    thread_id: str
    status: str  # "completed" | "awaiting_approval" | "error"
    answer: str = ""
    approval_request: dict | None = None
    trace: list[str] = []
    # Structured RCA deliverable (ranked hypotheses, evidence, severity,
    # postmortem), present once an investigation completes.
    report: dict | None = None
    # Total LLM tokens spent on this turn (cost visibility).
    tokens_used: int = 0


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


def _check_message(message: str) -> None:
    """Reject empty and over-long chat messages (an empty one would burn a full
    agent turn; the length cap bounds prompt/token cost)."""
    if not message or not message.strip():
        raise HTTPException(400, "Message cannot be empty.")
    cap = get_settings().copilot_max_message_chars
    if len(message) > cap:
        raise HTTPException(413, f"Message too long (max {cap} characters).")


def _to_response(thread_id: str, result: TurnResult) -> ChatResponse:
    return ChatResponse(
        thread_id=thread_id,
        status=result.status,
        answer=result.final_text,
        approval_request=result.approval_request,
        trace=result.trace,
        report=result.report,
        tokens_used=result.tokens_used,
    )


def _evictable_thread() -> str | None:
    """Pick a session safe to drop: least-recently-used among those NOT running a
    turn and NOT paused awaiting approval. None if every session is busy."""
    idle = [t for t in _SESSIONS if t not in _RUNNING and t not in _AWAITING]
    if not idle:
        return None
    return min(idle, key=lambda t: _LAST_USED.get(t, 0.0))


async def _get_session(thread_id: str) -> CopilotSession:
    """Return the live session for a thread, building one if absent.

    A rebuilt session shares the file-backed checkpointer, so it transparently
    resumes a thread whose in-memory session was evicted — state survives. The
    in-memory pool is bounded; eviction is LRU and skips busy/awaiting sessions
    so a paused-for-approval thread is never stranded.
    """
    session = _SESSIONS.get(thread_id)
    if session is not None:
        return session
    # Re-check under the lock so two concurrent first-requests don't both build one.
    async with _SESSION_LOCK:
        session = _SESSIONS.get(thread_id)
        if session is None:
            cap = get_settings().copilot_max_sessions
            while cap and len(_SESSIONS) >= cap:
                victim = _evictable_thread()
                if victim is None:  # all sessions busy — allow a temporary over-cap
                    log.warning("session cap %d reached but all sessions are busy", cap)
                    break
                await _evict(victim)
            session = await CopilotSession(thread_id=thread_id).__aenter__()
            _SESSIONS[thread_id] = session
            _LAST_USED[thread_id] = time.monotonic()
        return session


@app.get("/healthz")
async def healthz() -> dict:
    """Liveness: the process is up and serving."""
    return {"status": "ok", "active_sessions": len(_SESSIONS)}


@app.get("/readyz")
async def readyz():
    """Readiness: can we actually serve an investigation? In production we require
    an LLM API key to be configured (env or runtime) before reporting ready."""
    s = get_settings()
    reasons: list[str] = []
    if s.is_production and not active_api_key():
        reasons.append("no LLM API key configured for the active provider")
    ready = not reasons
    return JSONResponse(
        {"status": "ready" if ready else "not_ready", "reasons": reasons},
        status_code=200 if ready else 503,
    )


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
    if provider not in {"anthropic", "openai", "gemini", "groq", "deepseek"}:
        raise HTTPException(
            400, "provider must be one of: anthropic, openai, gemini, groq, deepseek"
        )

    snapshot = runtime.model_snapshot()
    runtime.set_model(provider, req.api_key, req.model, req.fast_model)
    if not active_api_key():
        # Roll back to the PRIOR working config (not .env), keeping other overrides.
        runtime.restore_model(snapshot)
        raise HTTPException(400, f"An API key is required for '{provider}'.")

    await _evict_all()  # rebuild sessions so the new model/key is used
    audit.record("config.model_changed", provider=runtime.provider())
    main_model, fast_model = resolved_models()
    return {"provider": runtime.provider(), "model": main_model, "fast_model": fast_model}


def _set_source(path: str, kind: str, setter) -> str:
    p = Path(path).expanduser()
    if not p.is_absolute():
        p = ROOT / p
    p = p.resolve()
    # Confine to the allowlisted root so a caller can't aim the repo/logs MCP
    # servers (and therefore the agent's file tools) at arbitrary host paths.
    root = get_settings().sources_root
    if p != root and root not in p.parents:
        raise HTTPException(403, f"path must be inside the allowed sources root ({root})")
    if not p.exists():
        raise HTTPException(404, f"path does not exist: {p}")
    if not p.is_dir():
        raise HTTPException(400, f"path is not a directory: {p}")
    setter(path.strip())
    return str(p)


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
    _check_message(req.message)
    existed = req.thread_id in _SESSIONS
    try:
        async with _running_turn(req.thread_id):
            session = await _get_session(req.thread_id)
            # Don't run a fresh message on a thread paused mid-approval: it would
            # leave the prior tool_calls unanswered and corrupt the history.
            if await session.pending_interrupt():
                _AWAITING.add(req.thread_id)
                raise HTTPException(
                    409,
                    "This thread is paused awaiting approval — approve or reject "
                    "the pending action (POST /approve) before sending a new message.",
                )
            _AWAITING.discard(req.thread_id)
            result = await session.ask(req.message)
            if result.status == "awaiting_approval":
                _AWAITING.add(req.thread_id)
    except HTTPException:
        raise  # 409/4xx should surface as real HTTP errors, not a 200 error body
    except Exception as exc:  # noqa: BLE001 — surface a clean error to the UI
        log.exception("chat turn failed (thread=%s)", req.thread_id)
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
    async with _running_turn(req.thread_id):
        existed = req.thread_id in _SESSIONS
        # Rebuild from the checkpointer if the session was evicted — state survives.
        session = await _get_session(req.thread_id)
        if not await session.pending_interrupt():
            if not existed:
                await _evict(req.thread_id)  # don't leak a session built just to check
            raise HTTPException(
                404, f"no investigation awaiting approval for thread '{req.thread_id}'"
            )
        audit.record("approval.decided", thread=req.thread_id, approved=req.approved)
        try:
            result = await session.resume(approved=req.approved, reason=req.reason)
            if result.status == "awaiting_approval":
                _AWAITING.add(req.thread_id)
            else:
                _AWAITING.discard(req.thread_id)
        except Exception as exc:  # noqa: BLE001
            log.exception("approve/resume failed (thread=%s)", req.thread_id)
            return ChatResponse(
                thread_id=req.thread_id, status="error", answer=_friendly_error(exc)
            )
    return _to_response(req.thread_id, result)


# --------------------------------------------------------------------------- #
# Streaming (SSE) — live trace as the agent investigates
# --------------------------------------------------------------------------- #
def _stream_payload(thread_id: str, ev: dict) -> dict:
    """Map an internal session event to the SSE wire shape the frontend expects."""
    t = ev["type"]
    out: dict = {"type": t, "thread_id": thread_id}
    if t == "trace":
        out["line"] = ev["line"]
    elif t == "approval":
        out.update(
            status="awaiting_approval",
            approval_request=ev["approval_request"],
            trace=ev["trace"],
        )
    elif t == "done":
        out.update(
            status="completed",
            answer=ev["final_text"],
            trace=ev["trace"],
            report=ev.get("report"),
            tokens_used=ev.get("tokens_used", 0),
        )
    return out


@app.post("/chat/stream")
async def chat_stream(req: ChatRequest):
    """Stream an investigation live (SSE): one event per graph step, then a
    terminal `approval` or `done` event."""
    _check_message(req.message)  # raise 400/413 before the stream opens

    async def gen():
        async with _running_turn(req.thread_id):
            existed = req.thread_id in _SESSIONS
            try:
                session = await _get_session(req.thread_id)
                if await session.pending_interrupt():
                    _AWAITING.add(req.thread_id)
                    yield {
                        "data": json.dumps(
                            {
                                "type": "error",
                                "thread_id": req.thread_id,
                                "status": "error",
                                "answer": "This thread is paused awaiting approval — "
                                "approve or reject the pending action first.",
                            }
                        )
                    }
                    return
                _AWAITING.discard(req.thread_id)
                async for ev in session.ask_stream(req.message):
                    t = ev.get("type")
                    if t == "approval":
                        _AWAITING.add(req.thread_id)
                    elif t == "done":
                        _AWAITING.discard(req.thread_id)
                    yield {"data": json.dumps(_stream_payload(req.thread_id, ev))}
            except Exception as exc:  # noqa: BLE001
                log.exception("chat stream failed (thread=%s)", req.thread_id)
                if not existed:
                    await _evict(req.thread_id)
                yield {
                    "data": json.dumps(
                        {
                            "type": "error",
                            "thread_id": req.thread_id,
                            "status": "error",
                            "answer": _friendly_error(exc),
                        }
                    )
                }

    return EventSourceResponse(gen())


@app.post("/approve/stream")
async def approve_stream(req: ApproveRequest):
    """Stream the resume of a paused (approval) turn via SSE."""

    async def gen():
        async with _running_turn(req.thread_id):
            existed = req.thread_id in _SESSIONS
            session = await _get_session(req.thread_id)  # rebuild from checkpointer if evicted
            if not await session.pending_interrupt():
                if not existed:
                    await _evict(req.thread_id)
                yield {
                    "data": json.dumps(
                        {
                            "type": "error",
                            "thread_id": req.thread_id,
                            "status": "error",
                            "answer": f"no investigation awaiting approval for thread '{req.thread_id}'",
                        }
                    )
                }
                return
            audit.record("approval.decided", thread=req.thread_id, approved=req.approved)
            try:
                async for ev in session.resume_stream(
                    approved=req.approved, reason=req.reason
                ):
                    t = ev.get("type")
                    if t == "approval":
                        _AWAITING.add(req.thread_id)
                    elif t == "done":
                        _AWAITING.discard(req.thread_id)
                    yield {"data": json.dumps(_stream_payload(req.thread_id, ev))}
            except Exception as exc:  # noqa: BLE001
                log.exception("approve stream failed (thread=%s)", req.thread_id)
                yield {
                    "data": json.dumps(
                        {
                            "type": "error",
                            "thread_id": req.thread_id,
                            "status": "error",
                            "answer": _friendly_error(exc),
                        }
                    )
                }

    return EventSourceResponse(gen())


@app.get("/metrics")
async def metrics() -> dict:
    """Real metric series + error summary from the active logs/metrics source."""
    return metrics_source.read_all()


@app.post("/feedback")
async def submit_feedback(req: FeedbackRequest) -> dict:
    """Capture a thumbs up/down on an investigation. Thumbs-down on a real failure
    is the seed for a regression eval case (the trust/learning loop)."""
    try:
        entry = feedback.record_feedback(req.thread_id, req.rating, req.comment, req.question)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return {"status": "recorded", "feedback": entry}


@app.get("/audit")
async def get_audit(limit: int = 100, event_prefix: str = "") -> dict:
    """Queryable audit trail: recent who/what/when events (approvals, model
    changes, prompt-injection detections, feedback), newest first. Auth-protected
    like every other route — the read half of the compliance requirement."""
    limit = max(1, min(limit, 1000))
    return {"events": audit.recent(limit=limit, event_prefix=event_prefix)}


# --------------------------------------------------------------------------- #
# Triggers — PagerDuty webhook -> investigate -> Slack approve/reject
# (the product loop: the agent shows up when you're paged, instead of being asked)
# --------------------------------------------------------------------------- #
async def _post_to_slack(thread_id: str, title: str, result: TurnResult) -> None:
    s = get_settings()
    if result.status == "awaiting_approval":
        req = result.approval_request or {}
        detail = "\n".join(
            f"• `{a.get('tool')}`" + (" *(write)*" if a.get("write") else "")
            for a in req.get("actions", [])
        ) or req.get("message", "")
        blocks, text = slack.approval_blocks(thread_id, title, detail), f"Approval needed: {title}"
    elif result.report:
        # Prefer the structured RCA verdict (severity/root-cause/actions at a glance).
        blocks = slack.report_blocks(title, result.report, result.final_text)
        text = f"Investigation: {title}"
    else:
        blocks, text = slack.result_blocks(title, result.final_text), f"Investigation: {title}"
    res = await slack.post_message(s.slack_bot_token, s.slack_channel, text, blocks)
    if not res.get("ok"):
        log.info("slack post skipped/failed (thread=%s): %s", thread_id, res.get("skipped") or res)


async def _run_triggered_investigation(incident: dict) -> None:
    thread_id = f"pd-{incident['id']}"
    title = incident.get("title") or thread_id
    seed = (
        f"A PagerDuty incident was triggered: {title}"
        + (f" (service: {incident['service']})" if incident.get("service") else "")
        + ". Investigate the root cause from logs, metrics, code, and recent changes, "
        "then propose a fix."
    )
    try:
        async with _running_turn(thread_id):
            session = await _get_session(thread_id)
            result = await session.ask(seed)
            if result.status == "awaiting_approval":
                _AWAITING.add(thread_id)
        await _post_to_slack(thread_id, title, result)
    except Exception:  # noqa: BLE001 — best-effort background trigger
        log.exception("triggered investigation failed (incident=%s)", incident.get("id"))


async def _resume_triggered(thread_id: str, approved: bool) -> None:
    try:
        async with _running_turn(thread_id):
            session = await _get_session(thread_id)
            if not await session.pending_interrupt():
                return
            audit.record("approval.decided", thread=thread_id, approved=approved, via="slack")
            result = await session.resume(approved=approved, reason="decided via Slack")
            if result.status == "awaiting_approval":
                _AWAITING.add(thread_id)
            else:
                _AWAITING.discard(thread_id)
        await _post_to_slack(thread_id, thread_id, result)
    except Exception:  # noqa: BLE001
        log.exception("slack-triggered resume failed (thread=%s)", thread_id)


@app.post("/webhooks/pagerduty")
async def pagerduty_webhook(request: Request):
    """PagerDuty v3 webhook → auto-start an investigation (HMAC-verified)."""
    raw = await request.body()
    s = get_settings()
    sig = request.headers.get("x-pagerduty-signature", "")
    if not s.pagerduty_webhook_secret or not pd_webhook.verify_signature(
        s.pagerduty_webhook_secret, raw, sig
    ):
        raise HTTPException(401, "invalid or unconfigured PagerDuty webhook signature")
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise HTTPException(400, "invalid JSON body") from exc
    incident = pd_webhook.parse_incident(payload)
    if not incident or not incident.get("id"):
        return {"status": "ignored"}
    if not incident["type"].endswith("triggered"):
        return {"status": "ignored", "event": incident["type"]}
    if not active_api_key():
        log.warning("PagerDuty incident %s received but no LLM key configured", incident["id"])
        return {"status": "accepted_no_llm", "incident": incident["id"]}
    asyncio.create_task(_run_triggered_investigation(incident))
    return {"status": "accepted", "incident": incident["id"]}


@app.post("/webhooks/slack/interactions")
async def slack_interactions(request: Request):
    """Slack interactive callback → map an Approve/Reject button to the resume."""
    raw = await request.body()
    s = get_settings()
    ts = request.headers.get("x-slack-request-timestamp", "")
    sig = request.headers.get("x-slack-signature", "")
    if not s.slack_signing_secret or not slack.verify_signature(
        s.slack_signing_secret, ts, raw, sig
    ):
        raise HTTPException(401, "invalid or unconfigured Slack signature")
    form = parse_qs(raw.decode())
    try:
        payload = json.loads(form.get("payload", ["{}"])[0])
    except json.JSONDecodeError as exc:
        raise HTTPException(400, "invalid interaction payload") from exc
    actions = payload.get("actions") or []
    if not actions:
        return {"text": "No action."}
    thread_id = actions[0].get("value") or ""
    approved = actions[0].get("action_id") == "approve"
    if active_api_key() and thread_id:
        asyncio.create_task(_resume_triggered(thread_id, approved))
    return {"text": f"{'Approved' if approved else 'Rejected'} — continuing the investigation."}


# --------------------------------------------------------------------------- #
# Static SPA — serve the built frontend so the whole app is one deployable.
# --------------------------------------------------------------------------- #
# Mounted LAST: the explicit API routes above are matched first; everything else
# (/, /assets/*, etc.) falls through to here. StaticFiles is a sub-application,
# so it bypasses the require_auth dependency — the browser loads the HTML/JS
# freely, then sends the bearer token (if any) on the API calls. In dev (no
# build) the dist dir is absent and this mount is simply skipped.
_FRONTEND_DIST = ROOT / "frontend" / "dist"
if _FRONTEND_DIST.is_dir():
    app.mount("/", StaticFiles(directory=str(_FRONTEND_DIST), html=True), name="spa")
    log.info("serving built frontend from %s", _FRONTEND_DIST)
