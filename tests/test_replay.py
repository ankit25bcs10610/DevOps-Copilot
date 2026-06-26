"""Deterministic record/replay cassette layer for LLM calls."""

import pytest
from langchain_core.messages import AIMessage, HumanMessage

from app import replay


class _StubModel:
    """Minimal chat-model stand-in: returns a fixed response, records bound tools."""

    def __init__(self, response):
        self._response = response
        self.bound: list[str] | None = None

    def bind_tools(self, tools, **kwargs):
        self.bound = [getattr(t, "name", str(t)) for t in tools]
        return self

    def invoke(self, messages, *args, **kwargs):
        return self._response


def test_off_mode_is_passthrough(monkeypatch):
    monkeypatch.setenv("COPILOT_REPLAY_MODE", "off")
    stub = _StubModel(AIMessage(content="x"))
    assert replay.wrap(stub, "m") is stub  # production path: untouched


def test_key_ignores_message_ids():
    k1 = replay.cassette_key("m", [HumanMessage(content="hi", id="a")], [])
    k2 = replay.cassette_key("m", [HumanMessage(content="hi", id="b")], [])
    assert k1 == k2  # ids excluded -> stable across runs


def test_key_changes_with_content_and_tools():
    base = replay.cassette_key("m", [HumanMessage(content="hi")], [])
    assert base != replay.cassette_key("m", [HumanMessage(content="bye")], [])
    assert base != replay.cassette_key("m", [HumanMessage(content="hi")], ["search_logs"])
    assert base != replay.cassette_key("other", [HumanMessage(content="hi")], [])


def test_record_then_replay_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setenv("COPILOT_CASSETTE_PATH", str(tmp_path / "c.json"))

    # Record a response from the real (stub) model.
    monkeypatch.setenv("COPILOT_REPLAY_MODE", "record")
    replay.reset_cassette()
    llm = replay.wrap(_StubModel(AIMessage(content="root cause: null deref")), "claude-opus-4-8")
    out = llm.invoke([HumanMessage(content="why 500s?")])
    assert out.content == "root cause: null deref"

    # Replay: a fresh cassette + a stub that would return something DIFFERENT.
    # Getting the recorded answer proves the cassette (not the stub) was used.
    monkeypatch.setenv("COPILOT_REPLAY_MODE", "replay")
    replay.reset_cassette()
    llm2 = replay.replay_model("claude-opus-4-8")
    out2 = llm2.invoke([HumanMessage(content="why 500s?")])
    assert out2.content == "root cause: null deref"


def test_replay_preserves_tool_calls(tmp_path, monkeypatch):
    monkeypatch.setenv("COPILOT_CASSETTE_PATH", str(tmp_path / "tc.json"))
    monkeypatch.setenv("COPILOT_REPLAY_MODE", "record")
    replay.reset_cassette()
    resp = AIMessage(
        content="",
        tool_calls=[{"name": "search_logs", "args": {"service": "checkout-svc"}, "id": "call_1"}],
    )
    llm = replay.wrap(_StubModel(resp), "m").bind_tools([type("T", (), {"name": "search_logs"})()])
    msgs = [HumanMessage(content="investigate")]
    llm.invoke(msgs)

    monkeypatch.setenv("COPILOT_REPLAY_MODE", "replay")
    replay.reset_cassette()
    llm2 = replay.replay_model("m").bind_tools([type("T", (), {"name": "search_logs"})()])
    out = llm2.invoke(msgs)
    assert out.tool_calls and out.tool_calls[0]["name"] == "search_logs"
    assert out.tool_calls[0]["args"]["service"] == "checkout-svc"


def test_replay_miss_raises(tmp_path, monkeypatch):
    monkeypatch.setenv("COPILOT_CASSETTE_PATH", str(tmp_path / "empty.json"))
    monkeypatch.setenv("COPILOT_REPLAY_MODE", "replay")
    replay.reset_cassette()
    llm = replay.replay_model("m")
    with pytest.raises(replay.ReplayMiss):
        llm.invoke([HumanMessage(content="never recorded")])
