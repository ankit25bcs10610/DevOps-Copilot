"""Routing logic — including the safety-critical write-approval gate."""

from types import SimpleNamespace

from app.graph.edges import route_after_agent, route_after_approval, route_after_reflect


def _ai(tool_calls):
    return SimpleNamespace(tool_calls=tool_calls)


def test_no_tool_calls_routes_to_reflect():
    assert route_after_agent({"messages": [_ai([])]}) == "reflect"


def test_read_only_tools_execute_immediately():
    state = {"messages": [_ai([{"name": "read_file"}, {"name": "get_metric"}])]}
    assert route_after_agent(state) == "tools"


def test_write_tool_requires_approval():
    state = {"messages": [_ai([{"name": "create_pull_request"}])]}
    assert route_after_agent(state) == "approval"


def test_write_mixed_with_reads_still_requires_approval():
    # The whole message runs on approval, so any write must gate the batch.
    state = {"messages": [_ai([{"name": "read_file"}, {"name": "create_pull_request"}])]}
    assert route_after_agent(state) == "approval"


def test_after_approval_approved_runs_tools():
    # Approved => the AIMessage with tool_calls is intact.
    state = {"messages": [_ai([{"name": "create_pull_request"}])]}
    assert route_after_approval(state) == "tools"


def test_after_approval_rejected_returns_to_agent():
    # Rejected => a ToolMessage (no tool_calls) was appended.
    state = {"messages": [SimpleNamespace(content="ACTION REJECTED")]}
    assert route_after_approval(state) == "agent"


def test_after_reflect_done_ends():
    assert route_after_reflect({"status": "done"}) == "__end__"


def test_after_reflect_continue_loops():
    assert route_after_reflect({"status": "investigating"}) == "agent"
