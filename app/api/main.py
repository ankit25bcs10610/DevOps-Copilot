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
import contextlib
import hashlib
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

from app import audit, feedback, metering, metrics_source, observability, runtime, tenant_context
from app.config import ROOT, get_settings
from app.integrations import pagerduty as pd_webhook
from app.integrations import slack
from app.llm import active_api_key, resolved_models
from app.session import CopilotSession, TurnResult
from app.tenancy import auth as tenant_auth
from app.tenancy import models as tn_models

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
            "search_logs", "get_error_summary", "cluster_logs", "get_metric", "list_services",
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
            "analyze_critical_path", "get_exemplars", "correlate_incidents",
        ],
    },
    {
        "name": "deploys",
        "label": "Deploys",
        "custom": True,
        "tools": ["list_deploys", "get_deploy", "deploys_in_window"],
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
    # Multi-tenant: ensure the tenant store schema exists before serving.
    if get_settings().copilot_multi_tenant:
        await tenant_auth.get_store().setup()
        log.info("multi-tenant mode: tenant store ready (%s)", get_settings().copilot_tenant_db)
        if get_settings().copilot_tenant_db.startswith(("postgres://", "postgresql://")) is False:
            log.warning("multi-tenant on SQLite is app-level isolated, not RLS-hard-isolated; "
                        "use Postgres + RLS for production tenant isolation")
    # Proactive SLO-burn poller (opt-in): auto-open investigations before a page.
    slo_task: asyncio.Task | None = None
    if get_settings().copilot_slo_poller:
        from app.mcp.servers.datadog import server as _dd
        from app.slo_poller import SLOPoller

        poller = SLOPoller(
            services_fn=_dd.list_services,
            burn_fn=_dd.compute_burn_rate,
            trigger_fn=_slo_trigger,
            interval=get_settings().copilot_slo_poll_interval_s,
            cooldown=get_settings().copilot_slo_cooldown_s,
        )
        slo_task = asyncio.create_task(poller.run())
        log.info("proactive SLO poller enabled")
    yield
    if slo_task:
        slo_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await slo_task
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
    # Self-serve signup is how a new tenant GETS its first credential, so it can't
    # require one. It stays out of _OPEN_PATHS so the IP rate-limiter still applies
    # (abuse guard); the handler itself enforces multi-tenant + the enabled flag.
    if request.url.path == "/signup":
        return

    s = get_settings()
    # Multi-tenant: resolve the per-tenant API key (dcp_…) into the request's
    # TenantConfig + actor on the contextvar. Task-isolated, so it's scoped to
    # this request and read by runtime.py / the agent for the rest of the call.
    if s.copilot_multi_tenant:
        provided = request.headers.get("authorization", "")
        token = provided[7:].strip() if provided.startswith("Bearer ") else provided.strip()
        # A `dcp_…` value is a tenant API key; anything with two dots is a JWT
        # (Supabase/SSO login). Both resolve to the same TenantConfig + actor.
        if token.startswith("dcp_"):
            resolved = await tenant_auth.resolve(token)
        elif token.count(".") == 2:
            resolved = await tenant_auth.resolve_jwt(token)
        else:
            resolved = None
        if resolved is None:
            raise HTTPException(401, "Invalid or revoked credential.")
        cfg, actor = resolved
        tenant_context.set_tenant(cfg)
        tenant_context.set_actor(actor)
        return

    expected = s.copilot_api_token
    if not expected:
        return  # auth disabled (dev)
    provided = request.headers.get("authorization", "")
    # Constant-time compare so the token can't be recovered via response timing.
    if not hmac.compare_digest(provided, f"Bearer {expected}"):
        raise HTTPException(401, "Missing or invalid API token.")


def require_perm(action: str):
    """Route dependency factory: enforce the RBAC matrix on the resolved tenant.
    No-op in single-tenant mode (no roles), so the offline demo is unaffected."""

    async def _check() -> None:
        if not get_settings().copilot_multi_tenant:
            return
        cfg = tenant_context.get_tenant()
        role = cfg.role if cfg else "viewer"
        if not tn_models.can(role, action):
            raise HTTPException(403, f"Role '{role}' is not permitted to {action.replace('_', ' ')}.")

    return _check


def _scoped(thread_id: str) -> str:
    """Namespace a thread by tenant so one org can never resume/read another's
    investigation. No-op (returns thread_id) in single-tenant mode."""
    cfg = tenant_context.get_tenant()
    return f"{cfg.org_id}:{thread_id}" if cfg else thread_id


async def quota_gate() -> None:
    """Route dependency: block a new investigation when the tenant is over its
    monthly plan quota (402). No-op in single-tenant mode."""
    if await metering.over_quota():
        raise HTTPException(
            402, "Monthly investigation quota reached for your plan — upgrade to continue."
        )


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


# --- admin / tenant-management request bodies (multi-tenant) --------------- #
class CreateApiKeyRequest(BaseModel):
    name: str = ""
    role: str = "responder"


class AddMemberRequest(BaseModel):
    email: str
    role: str = "viewer"


class SetIntegrationRequest(BaseModel):
    name: str
    value: str


class SetPlanRequest(BaseModel):
    plan: str


class SignupRequest(BaseModel):
    org_name: str
    email: str


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
    # In multi-tenant mode each tenant supplies its own LLM key, so a global key
    # isn't required for readiness.
    if s.is_production and not s.copilot_multi_tenant and not active_api_key():
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


def _normalize_repo(raw: str) -> str:
    """Accept a full GitHub URL or `owner/repo` and return `owner/repo`.
    Strips https/ssh prefixes, a trailing .git, and any extra path segments —
    so pasting 'https://github.com/acme/app' works, not just 'acme/app'."""
    r = (raw or "").strip()
    r = r.removesuffix(".git")
    for prefix in ("https://github.com/", "http://github.com/", "github.com/", "git@github.com:"):
        if r.startswith(prefix):
            r = r[len(prefix):]
            break
    parts = [p for p in r.strip("/").split("/") if p]
    return "/".join(parts[:2])  # owner/repo


@app.post("/github/connect", dependencies=[Depends(require_perm("manage_integrations"))])
async def github_connect(req: GithubConnectRequest) -> dict:
    """Validate a GitHub token + repo against the real API, then switch the
    GitHub MCP server into live mode (in-memory only — not persisted)."""
    token = req.token.strip()
    repo = _normalize_repo(req.repo)  # accept a URL or owner/repo
    if not token or repo.count("/") != 1:
        raise HTTPException(400, "Provide a token and a repo as 'owner/repo' (or a GitHub URL).")

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


@app.post("/github/disconnect", dependencies=[Depends(require_perm("manage_integrations"))])
async def github_disconnect() -> dict:
    runtime.clear_github()
    await _evict_all()
    return _github_status()


@app.post("/model/configure", dependencies=[Depends(require_perm("manage_integrations"))])
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


@app.post("/sources/repo", dependencies=[Depends(require_perm("manage_integrations"))])
async def set_repo_source(req: SourcePathRequest) -> dict:
    resolved = _set_source(req.path, "repo", runtime.set_repo_path)
    await _evict_all()
    return {"repo_path": resolved}


@app.post("/sources/logs", dependencies=[Depends(require_perm("manage_integrations"))])
async def set_logs_source(req: SourcePathRequest) -> dict:
    resolved = _set_source(req.path, "logs", runtime.set_logs_path)
    # Soft warning if the expected demo files aren't present.
    p = Path(resolved)
    missing = [f for f in ("app.log", "metrics.json") if not (p / f).exists()]
    await _evict_all()
    return {"logs_path": resolved, "missing_files": missing}


@app.post("/reset", dependencies=[Depends(require_perm("manage_integrations"))])
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


@app.post("/chat", response_model=ChatResponse,
          dependencies=[Depends(require_perm("run_investigation")), Depends(quota_gate)])
async def chat(req: ChatRequest) -> ChatResponse:
    _check_message(req.message)
    tid = _scoped(req.thread_id)  # tenant-namespaced internal key (client sees its own id)
    existed = tid in _SESSIONS
    try:
        async with _running_turn(tid):
            session = await _get_session(tid)
            # Don't run a fresh message on a thread paused mid-approval: it would
            # leave the prior tool_calls unanswered and corrupt the history.
            if await session.pending_interrupt():
                _AWAITING.add(tid)
                raise HTTPException(
                    409,
                    "This thread is paused awaiting approval — approve or reject "
                    "the pending action (POST /approve) before sending a new message.",
                )
            _AWAITING.discard(tid)
            result = await session.ask(req.message)
            if result.status == "awaiting_approval":
                _AWAITING.add(tid)
            elif result.status == "completed":
                await metering.record_investigation(result.tokens_used)
    except HTTPException:
        raise  # 409/4xx should surface as real HTTP errors, not a 200 error body
    except Exception as exc:  # noqa: BLE001 — surface a clean error to the UI
        log.exception("chat turn failed (thread=%s)", tid)
        # If this session was just created and its first turn failed, drop it so
        # a retry rebuilds cleanly instead of reusing a half-initialized session.
        if not existed:
            await _evict(tid)
        return ChatResponse(
            thread_id=req.thread_id, status="error", answer=_friendly_error(exc)
        )
    return _to_response(req.thread_id, result)


@app.post("/approve", response_model=ChatResponse,
          dependencies=[Depends(require_perm("approve_action"))])
async def approve(req: ApproveRequest) -> ChatResponse:
    tid = _scoped(req.thread_id)
    async with _running_turn(tid):
        existed = tid in _SESSIONS
        # Rebuild from the checkpointer if the session was evicted — state survives.
        session = await _get_session(tid)
        if not await session.pending_interrupt():
            if not existed:
                await _evict(tid)  # don't leak a session built just to check
            raise HTTPException(
                404, f"no investigation awaiting approval for thread '{req.thread_id}'"
            )
        audit.record("approval.decided", thread=req.thread_id, approved=req.approved)
        try:
            result = await session.resume(approved=req.approved, reason=req.reason)
            if result.status == "awaiting_approval":
                _AWAITING.add(tid)
            else:
                _AWAITING.discard(tid)
                if result.status == "completed":
                    await metering.record_investigation(result.tokens_used)
        except Exception as exc:  # noqa: BLE001
            log.exception("approve/resume failed (thread=%s)", tid)
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


@app.post("/chat/stream",
          dependencies=[Depends(require_perm("run_investigation")), Depends(quota_gate)])
async def chat_stream(req: ChatRequest):
    """Stream an investigation live (SSE): one event per graph step, then a
    terminal `approval` or `done` event."""
    _check_message(req.message)  # raise 400/413 before the stream opens
    tid = _scoped(req.thread_id)
    # Capture the tenant here (contextvar reliably set in the auth dependency) and
    # re-establish it inside the generator, which may be iterated after the request
    # scope unwinds (SSE streaming behind middleware).
    cfg = tenant_context.get_tenant()

    async def gen():
        tok = tenant_context.set_tenant(cfg)
        try:
            async with _running_turn(tid):
                existed = tid in _SESSIONS
                try:
                    session = await _get_session(tid)
                    if await session.pending_interrupt():
                        _AWAITING.add(tid)
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
                    _AWAITING.discard(tid)
                    async for ev in session.ask_stream(req.message):
                        t = ev.get("type")
                        if t == "approval":
                            _AWAITING.add(tid)
                        elif t == "done":
                            _AWAITING.discard(tid)
                            await metering.record_investigation(ev.get("tokens_used", 0))
                        yield {"data": json.dumps(_stream_payload(req.thread_id, ev))}
                except Exception as exc:  # noqa: BLE001
                    log.exception("chat stream failed (thread=%s)", tid)
                    if not existed:
                        await _evict(tid)
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
        finally:
            tenant_context.reset_tenant(tok)

    return EventSourceResponse(gen())


@app.post("/approve/stream", dependencies=[Depends(require_perm("approve_action"))])
async def approve_stream(req: ApproveRequest):
    """Stream the resume of a paused (approval) turn via SSE."""
    tid = _scoped(req.thread_id)
    cfg = tenant_context.get_tenant()

    async def gen():
        tok = tenant_context.set_tenant(cfg)
        try:
            async with _running_turn(tid):
                existed = tid in _SESSIONS
                session = await _get_session(tid)  # rebuild from checkpointer if evicted
                if not await session.pending_interrupt():
                    if not existed:
                        await _evict(tid)
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
                            _AWAITING.add(tid)
                        elif t == "done":
                            _AWAITING.discard(tid)
                            await metering.record_investigation(ev.get("tokens_used", 0))
                        yield {"data": json.dumps(_stream_payload(req.thread_id, ev))}
                except Exception as exc:  # noqa: BLE001
                    log.exception("approve stream failed (thread=%s)", tid)
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
        finally:
            tenant_context.reset_tenant(tok)

    return EventSourceResponse(gen())


@app.get("/metrics")
async def metrics() -> dict:
    """Real metric series + error summary from the active logs/metrics source."""
    return metrics_source.read_all()


@app.get("/usage")
async def usage() -> dict:
    """Current-period usage + quota for the tenant (cost/limit transparency).
    In single-tenant mode there are no per-tenant quotas, so it reports that."""
    summary = await metering.usage_summary()
    if summary is None:
        return {"multi_tenant": False, "detail": "usage quotas apply in multi-tenant mode only"}
    return summary


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


@app.get("/audit/verify")
async def audit_verify() -> dict:
    """Verify the audit hash-chain is intact (tamper-evidence) — reports the first
    broken link if any. The integrity artifact auditors request."""
    return audit.verify_chain()


# --------------------------------------------------------------------------- #
# Admin / tenant self-management (multi-tenant only; RBAC-gated).
# Org provisioning (the first owner key) is done out-of-band via the CLI:
#   python -m app.cli provision-org --name "Acme" --email owner@acme.com
# --------------------------------------------------------------------------- #
def _current_org_id() -> str:
    cfg = tenant_context.get_tenant()
    if cfg is None:
        raise HTTPException(400, "Admin endpoints require multi-tenant mode + a tenant API key.")
    return cfg.org_id


@app.get("/admin/org", dependencies=[Depends(require_perm("view"))])
async def admin_org() -> dict:
    org_id = _current_org_id()
    store = tenant_auth.get_store()
    org = await store.get_org(org_id)
    if org is None:
        raise HTTPException(404, "org not found")
    return {
        "id": org.id, "name": org.name, "plan": org.plan,
        "members": await store.count_members(org_id),
        "active_api_keys": await store.count_active_api_keys(org_id),
        "integrations": await store.count_integrations(org_id),
    }


@app.get("/admin/api-keys", dependencies=[Depends(require_perm("manage_api_keys"))])
async def admin_list_api_keys() -> dict:
    org_id = _current_org_id()
    keys = await tenant_auth.get_store().list_api_keys(org_id)
    return {"api_keys": [
        {"id": k.id, "prefix": k.prefix, "name": k.name, "role": k.role,
         "created_at": k.created_at, "last_used_at": k.last_used_at, "active": k.active}
        for k in keys
    ]}


@app.post("/admin/api-keys", dependencies=[Depends(require_perm("manage_api_keys"))])
async def admin_create_api_key(req: CreateApiKeyRequest) -> dict:
    org_id = _current_org_id()
    store = tenant_auth.get_store()
    cfg = tenant_context.get_tenant()
    plan = cfg.plan if cfg else "free"
    if not tn_models.within_quota(plan, "api_keys", await store.count_active_api_keys(org_id)):
        raise HTTPException(402, "Active API-key quota reached for your plan.")
    plaintext, rec = await store.issue_api_key(
        org_id, req.name, role=tn_models.normalize_role(req.role)
    )
    audit.record("apikey.created", org=org_id, key_id=rec.id, role=rec.role)
    return {"api_key": plaintext, "id": rec.id, "role": rec.role, "name": rec.name,
            "note": "Store this now — the secret is shown only once."}


@app.delete("/admin/api-keys/{key_id}", dependencies=[Depends(require_perm("manage_api_keys"))])
async def admin_revoke_api_key(key_id: str) -> dict:
    org_id = _current_org_id()
    revoked = await tenant_auth.get_store().revoke_api_key(key_id, org_id=org_id)
    if not revoked:
        raise HTTPException(404, "key not found (or already revoked) in this org")
    audit.record("apikey.revoked", org=org_id, key_id=key_id)
    return {"status": "revoked", "id": key_id}


@app.post("/admin/members", dependencies=[Depends(require_perm("manage_members"))])
async def admin_add_member(req: AddMemberRequest) -> dict:
    org_id = _current_org_id()
    store = tenant_auth.get_store()
    cfg = tenant_context.get_tenant()
    plan = cfg.plan if cfg else "free"
    if not tn_models.within_quota(plan, "seats", await store.count_members(org_id)):
        raise HTTPException(402, "Seat quota reached for your plan.")
    user = await store.create_user(req.email)
    m = await store.add_member(org_id, user.id, tn_models.normalize_role(req.role))
    audit.record("member.added", org=org_id, role=m.role)
    return {"email": user.email, "role": m.role}


@app.get("/admin/integrations", dependencies=[Depends(require_perm("manage_integrations"))])
async def admin_list_integrations() -> dict:
    org_id = _current_org_id()
    secrets = await tenant_auth.get_store().get_integration_secrets(org_id)
    return {"integrations": sorted(secrets.keys())}  # names only — never values


@app.post("/admin/integrations", dependencies=[Depends(require_perm("manage_integrations"))])
async def admin_set_integration(req: SetIntegrationRequest) -> dict:
    org_id = _current_org_id()
    name = req.name.strip()
    if not name:
        raise HTTPException(400, "integration name is required")
    await tenant_auth.get_store().set_integration_secret(org_id, name, req.value)
    audit.record("secret.write", org=org_id, name=name)
    return {"status": "saved", "name": name}


@app.patch("/admin/plan", dependencies=[Depends(require_perm("manage_billing"))])
async def admin_set_plan(req: SetPlanRequest) -> dict:
    org_id = _current_org_id()
    plan = tn_models.normalize_plan(req.plan)
    await tenant_auth.get_store().set_plan(org_id, plan)
    audit.record("plan.changed", org=org_id, plan=plan)
    return {"plan": plan}


# --------------------------------------------------------------------------- #
# Self-serve onboarding — a new tenant creates an org + owner key with no prior
# credential (the entry point for product signup). Auth-exempt (see require_auth)
# but still IP rate-limited. Always provisions the FREE plan; upgrades go through
# billing/admin so signup can't self-assign a paid tier.
# --------------------------------------------------------------------------- #
@app.post("/signup")
async def signup(req: SignupRequest) -> dict:
    s = get_settings()
    if not s.copilot_multi_tenant:
        raise HTTPException(400, "Self-serve signup requires multi-tenant mode (COPILOT_MULTI_TENANT=true).")
    if not s.copilot_signup_enabled:
        raise HTTPException(403, "Self-serve signup is disabled on this deployment.")
    name = (req.org_name or "").strip()
    email = (req.email or "").strip().lower()
    if not name or len(name) > 120:
        raise HTTPException(400, "Provide an organization name (1–120 chars).")
    if "@" not in email or "." not in email.split("@")[-1] or len(email) > 254:
        raise HTTPException(400, "Provide a valid email address.")
    store = tenant_auth.get_store()  # schema created at startup (lifespan)
    # One org per email: caps free-tier farming on this unauthenticated path and
    # refuses to bind an already-registered identity to a fresh org. (A never-seen
    # email can still be claimed here — real email verification before owner binding
    # needs mail infra and is deferred; disable signup or require SSO when that
    # threat matters. See docs/COMMERCIALIZATION.md.)
    if await store.get_membership_by_email(email) is not None:
        raise HTTPException(409, "An account already exists for this email.")
    org = await store.create_org(name, plan="free", owner_email=email)
    api_key, rec = await store.issue_api_key(org.id, name="owner-key", role="owner")
    audit.record("org.signup", org=org.id, key_id=rec.id, plan=org.plan)
    return {
        "org_id": org.id,
        "org_name": org.name,
        "plan": org.plan,
        "role": "owner",
        "api_key": api_key,
        "note": "Store this key now — it is shown only once. Use it as a Bearer token.",
    }


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
            elif result.status == "completed":
                # Meter the triggered path the same as the interactive one (no-op
                # without a tenant); idem-keyed by the incident so a re-run can't double-bill.
                await metering.record_investigation(result.tokens_used, idem=thread_id)
        await _post_to_slack(thread_id, title, result)
    except Exception:  # noqa: BLE001 — best-effort background trigger
        log.exception("triggered investigation failed (incident=%s)", incident.get("id"))


async def _slo_trigger(service: str, burn: dict) -> None:
    """Adapt an SLO burn alert into the shared triggered-investigation path."""
    verdict = burn.get("verdict", "burn-rate alert")
    incident = {
        "id": f"slo-{service}",
        "title": f"SLO burn alert: {service} — {verdict}",
        "service": service,
    }
    log.info("SLO poller opening investigation for %s (%s)", service, verdict)
    await _run_triggered_investigation(incident)


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


# Webhook delivery idempotency: dedup a provider redelivery (identical signed body)
# within a TTL window so an incident isn't investigated — or resumed — twice.
_SEEN_DELIVERIES: dict[str, float] = {}
_DELIVERY_TTL = 600  # seconds


def _claim_delivery(raw: bytes) -> bool:
    """True if this webhook body is new (claims it); False if a duplicate within TTL."""
    now = time.time()
    digest = hashlib.sha256(raw).hexdigest()
    if len(_SEEN_DELIVERIES) > 5000:  # bound memory: drop expired entries
        for k in [k for k, t in _SEEN_DELIVERIES.items() if now - t > _DELIVERY_TTL]:
            del _SEEN_DELIVERIES[k]
    prev = _SEEN_DELIVERIES.get(digest)
    if prev is not None and now - prev < _DELIVERY_TTL:
        return False
    _SEEN_DELIVERIES[digest] = now
    return True


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
    if not _claim_delivery(raw):
        return {"status": "duplicate", "incident": incident["id"]}  # redelivery — already running
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
    if not _claim_delivery(raw):
        return {"text": "Already processed."}  # duplicate Slack delivery
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
