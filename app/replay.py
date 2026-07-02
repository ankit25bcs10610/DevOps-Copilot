"""Deterministic record/replay (VCR-style cassettes) for LLM calls.

An agent is non-deterministic only because of the LLM — the MCP servers already
run deterministic offline fixtures. So to make a whole investigation reproducible
(for debugging, regression eval, and CI gating) we only need to record and replay
the LLM responses. This module wraps the chat model so that, keyed on a NORMALIZED
hash of the input messages (ids/timestamps excluded), each call can:

    off      — passthrough to the real model (default; production is untouched).
    record   — call the real model, save (input-hash -> response) to a cassette.
    replay   — return the recorded response with NO network/key required.

Replay is the foundation for the golden-trajectory eval gate (evals/run_golden.py):
record ~once with a live key, then replay forever in CI offline.

Controlled by env:
    COPILOT_REPLAY_MODE   = off | record | replay   (default off)
    COPILOT_CASSETTE_PATH = path to the cassette JSON (default evals/cassettes/default.json)
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
from pathlib import Path
from typing import Any

from langchain_core.messages import BaseMessage, message_to_dict, messages_from_dict

log = logging.getLogger("devcopilot.replay")

# LangChain stamps a fresh random id (``lc_<uuid>``) onto content blocks — e.g. when
# a tool returns a list, each item becomes a block carrying its own id. Those ids are
# non-semantic and change every run, so they must be scrubbed from the cassette KEY
# or replay never matches. (The stored response is untouched; only the key is.)
_VOLATILE_ID = re.compile(r"lc_[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")


def mode() -> str:
    return os.environ.get("COPILOT_REPLAY_MODE", "off").strip().lower()


def _cassette_path() -> Path:
    return Path(os.environ.get("COPILOT_CASSETTE_PATH", "evals/cassettes/default.json")).resolve()


class ReplayMiss(RuntimeError):
    """Raised in replay mode when no recorded response matches a request — i.e. the
    cassette is stale and must be re-recorded."""


def _normalize_message(m: BaseMessage) -> dict:
    """A stable, id/timestamp-free view of a message for keying. Two runs that
    produce the same logical conversation hash identically."""
    tool_calls = getattr(m, "tool_calls", None) or []
    norm_tc = sorted(
        ({"name": c.get("name"), "args": c.get("args")} for c in tool_calls),
        key=lambda c: json.dumps(c, sort_keys=True, default=str),
    )
    content = m.content if isinstance(m.content, str) else json.dumps(m.content, sort_keys=True, default=str)
    content = _VOLATILE_ID.sub("lc_", content)  # scrub run-varying content-block ids
    return {
        "type": m.__class__.__name__,
        "content": content,
        "tool_calls": _VOLATILE_ID.sub("lc_", json.dumps(norm_tc, sort_keys=True, default=str)),
        "tool_call_id": getattr(m, "tool_call_id", None),
        "name": getattr(m, "name", None),
    }


def cassette_key(model_id: str, messages: list[BaseMessage], tool_names: list[str]) -> str:
    """Deterministic SHA-256 over (model, bound tools, normalized messages)."""
    payload = {
        "model": model_id,
        "tools": sorted(tool_names or []),
        "messages": [_normalize_message(m) for m in messages],
    }
    blob = json.dumps(payload, sort_keys=True, default=str)
    return hashlib.sha256(blob.encode()).hexdigest()


class Cassette:
    """A JSON-backed store of {request_key: serialized_response}."""

    def __init__(self, path: Path):
        self.path = path
        self._data: dict[str, Any] = {}
        if path.exists():
            try:
                self._data = json.loads(path.read_text())
            except (OSError, ValueError):
                self._data = {}

    def get(self, key: str) -> dict | None:
        return self._data.get(key)

    def put(self, key: str, value: dict) -> None:
        self._data[key] = value

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(self._data, indent=2, sort_keys=True, default=str))

    def __len__(self) -> int:
        return len(self._data)


_CASSETTE: Cassette | None = None


def _cassette() -> Cassette:
    global _CASSETTE
    if _CASSETTE is None:
        _CASSETTE = Cassette(_cassette_path())
    return _CASSETTE


def reset_cassette() -> None:
    """Drop the cached cassette (tests set a fresh path then call this)."""
    global _CASSETTE
    _CASSETTE = None


class _CassetteLLM:
    """Duck-typed wrapper exposing the slice of the chat-model interface the graph
    uses (`invoke`, `bind_tools`). Records or replays each `invoke` by message hash.
    Only instantiated when COPILOT_REPLAY_MODE != off, so production is unaffected."""

    def __init__(self, inner: Any, model_id: str, tool_names: list[str] | None = None):
        self._inner = inner
        self._model_id = model_id
        self._tool_names = tool_names or []

    def bind_tools(self, tools: Any, **kwargs: Any) -> "_CassetteLLM":
        names = [getattr(t, "name", str(t)) for t in tools]
        # In replay-only mode there is no real client to bind (and no key to build one).
        inner = self._inner.bind_tools(tools, **kwargs) if self._inner is not None else None
        return _CassetteLLM(inner, self._model_id, names)

    def invoke(self, messages: Any, *args: Any, **kwargs: Any) -> Any:
        msg_list = list(messages) if isinstance(messages, (list, tuple)) else [messages]
        key = cassette_key(self._model_id, msg_list, self._tool_names)
        cur = mode()
        if cur == "replay":
            rec = _cassette().get(key)
            if rec is None:
                raise ReplayMiss(
                    f"no recorded LLM response for key {key[:12]}… (model={self._model_id}, "
                    f"tools={len(self._tool_names)}). Re-record the cassette."
                )
            return messages_from_dict([rec["response"]])[0]
        resp = self._inner.invoke(messages, *args, **kwargs)
        if cur == "record":
            _cassette().put(key, {"response": message_to_dict(resp)})
            _cassette().save()
        return resp

    def __getattr__(self, name: str) -> Any:
        # Forward anything else (e.g. .with_structured_output) to the real model.
        inner = self.__dict__.get("_inner")
        if inner is None:
            raise AttributeError(name)
        return getattr(inner, name)


def wrap(model: Any, model_id: str) -> Any:
    """Wrap a real chat model for RECORD — or return it unchanged when off.
    (Replay is handled by replay_model(), which needs no real client/key.)"""
    if mode() == "record":
        return _CassetteLLM(model, model_id)
    return model


def replay_model(model_id: str) -> Any:
    """A replay-only model: serves recorded responses with no real client/key."""
    return _CassetteLLM(None, model_id)
