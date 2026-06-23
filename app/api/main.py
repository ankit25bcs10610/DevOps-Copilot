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

from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from app.session import CopilotSession, TurnResult

# thread_id -> live session
_SESSIONS: dict[str, CopilotSession] = {}


@asynccontextmanager
async def lifespan(app: FastAPI):
    yield
    # Clean up any sessions still open at shutdown.
    for session in list(_SESSIONS.values()):
        await session.__aexit__(None, None, None)
    _SESSIONS.clear()


app = FastAPI(title="DevOps Copilot", version="0.1.0", lifespan=lifespan)

# Allow the React dev server (and a configurable prod origin) to call the API.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
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
    if session is None:
        if not create:
            raise HTTPException(404, f"no active session for thread '{thread_id}'")
        session = await CopilotSession(thread_id=thread_id).__aenter__()
        _SESSIONS[thread_id] = session
    return session


@app.get("/healthz")
async def healthz() -> dict:
    return {"status": "ok", "active_sessions": len(_SESSIONS)}


@app.post("/chat", response_model=ChatResponse)
async def chat(req: ChatRequest) -> ChatResponse:
    session = await _get_session(req.thread_id, create=True)
    try:
        result = await session.ask(req.message)
    except Exception as exc:  # noqa: BLE001 — surface a clean error to the UI
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
