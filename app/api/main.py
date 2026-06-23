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


class ChatRequest(BaseModel):
    thread_id: str
    message: str


class ApproveRequest(BaseModel):
    thread_id: str
    approved: bool
    reason: str = ""


class ChatResponse(BaseModel):
    thread_id: str
    status: str  # "completed" | "awaiting_approval"
    answer: str = ""
    approval_request: dict | None = None
    trace: list[str] = []


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
    result = await session.ask(req.message)
    return _to_response(req.thread_id, result)


@app.post("/approve", response_model=ChatResponse)
async def approve(req: ApproveRequest) -> ChatResponse:
    session = await _get_session(req.thread_id, create=False)
    result = await session.resume(approved=req.approved, reason=req.reason)
    return _to_response(req.thread_id, result)
