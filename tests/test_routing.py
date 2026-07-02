"""Model routing — difficulty classification + agent-node triage."""

from langchain_core.messages import AIMessage, HumanMessage

from app import routing


def test_incident_language_is_complex():
    for q in [
        "Why is the checkout API throwing 500 errors?",
        "checkout-svc is crashlooping, investigate",
        "error rate spiked after the last deploy",
        "the payments service is down",
        "there's a memory leak causing timeouts",
    ]:
        assert routing.classify_difficulty(q) == "complex", q


def test_informational_language_is_simple():
    for q in [
        "Which services are emitting logs right now?",
        "list the recent deploys",
        "show me the current status",
        "how many pods are running?",
        "give me an overview of the cluster",
    ]:
        assert routing.classify_difficulty(q) == "simple", q


def test_ties_and_unknowns_default_to_complex():
    # "list the errors" contains both — complex must win (never under-power).
    assert routing.classify_difficulty("list the errors from checkout") == "complex"
    assert routing.classify_difficulty("") == "complex"
    assert routing.classify_difficulty("do the thing with the stuff") == "complex"


def test_use_fast_model_respects_flag():
    assert routing.use_fast_model("which services exist?", routing_enabled=True) is True
    assert routing.use_fast_model("which services exist?", routing_enabled=False) is False
    assert routing.use_fast_model("why is it failing?", routing_enabled=True) is False


def _stub_llms(monkeypatch):
    from app.graph import nodes

    used: dict = {}

    class _M:
        def __init__(self, fast):
            self.fast = fast

        def bind_tools(self, _tools):
            return self

        def invoke(self, _msgs):
            used["fast"] = self.fast
            return AIMessage(content="answer")

    monkeypatch.setattr(nodes, "get_llm", lambda fast=False: _M(fast))
    return used


def test_agent_node_triages_simple_request_to_fast_model(monkeypatch):
    from app.config import get_settings
    from app.graph.nodes import make_agent_node

    used = _stub_llms(monkeypatch)
    monkeypatch.setattr(get_settings(), "copilot_model_routing", True)
    node = make_agent_node([])
    node({"messages": [HumanMessage(content="which services are emitting logs?")], "iteration": 0})
    assert used["fast"] is True


def test_agent_node_keeps_incident_on_main_model(monkeypatch):
    from app.config import get_settings
    from app.graph.nodes import make_agent_node

    used = _stub_llms(monkeypatch)
    monkeypatch.setattr(get_settings(), "copilot_model_routing", True)
    node = make_agent_node([])
    node({"messages": [HumanMessage(content="why is checkout throwing 500 errors?")], "iteration": 0})
    assert used["fast"] is False


def test_agent_node_routing_disabled_always_main(monkeypatch):
    from app.config import get_settings
    from app.graph.nodes import make_agent_node

    used = _stub_llms(monkeypatch)
    monkeypatch.setattr(get_settings(), "copilot_model_routing", False)
    node = make_agent_node([])
    node({"messages": [HumanMessage(content="which services exist?")], "iteration": 0})
    assert used["fast"] is False
