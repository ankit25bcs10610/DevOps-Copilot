"""Pure helpers in the graph nodes (the LLM-driven nodes need a key, so we test
the deterministic logic around them)."""

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from app.graph.nodes import _history_digest


def test_history_digest_keeps_prior_qa_drops_tools_and_current_request():
    msgs = [
        HumanMessage(content="why 500s?"),
        AIMessage(content="", tool_calls=[{"name": "search_logs", "args": {}, "id": "1"}]),
        ToolMessage(content="a huge wall of log output", tool_call_id="1"),
        AIMessage(content="Root cause: null deref in applyDiscount."),
        HumanMessage(content="now propose the fix"),  # current request — excluded
    ]
    d = _history_digest({"messages": msgs})
    assert "why 500s?" in d  # prior user question kept
    assert "Root cause: null deref in applyDiscount." in d  # prior final answer kept
    assert "now propose the fix" not in d  # current request excluded
    assert "huge wall of log output" not in d  # tool output excluded


def test_history_digest_empty_on_first_turn():
    assert _history_digest({"messages": [HumanMessage(content="first question")]}) == ""
    assert _history_digest({"messages": []}) == ""
