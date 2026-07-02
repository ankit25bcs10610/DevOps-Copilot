"""Seed the golden cassette OFFLINE with a deterministic scripted agent.

The golden gate replays recorded LLM responses so CI can catch trajectory/scorer
regressions without a key. Normally you record once from a live LLM
(`run_golden --record`). This script instead drives the real graph + real MCP
fixtures with a *scripted* model, producing a genuine, replayable cassette with no
paid key — so the gate is live out of the box. Re-record from a live LLM anytime to
capture real model trajectories (the cassette format is identical).

    uv run python -m evals.record_golden_offline

It writes evals/cassettes/golden.json for the cases in evals/golden_cases.yaml.
"""

from __future__ import annotations

import asyncio
import os
from pathlib import Path

# Must be set BEFORE importing app modules so the replay layer records.
os.environ["COPILOT_REPLAY_MODE"] = "record"
os.environ.setdefault("COPILOT_CASSETTE_PATH", "evals/cassettes/golden.json")
os.environ["COPILOT_GOLDEN_CASES"] = "evals/golden_cases.yaml"
# Keep the corpus/report deterministic while recording: no learned-incident drift.
os.environ["COPILOT_LEARN_INCIDENTS"] = "false"

import yaml  # noqa: E402
from langchain_core.messages import AIMessage, ToolMessage  # noqa: E402

from app import llm as llm_mod  # noqa: E402
from app import replay  # noqa: E402
from app.graph import nodes  # noqa: E402

# Deterministic RCA the reporter "returns" for the informational golden case.
_REPORT_JSON = (
    '{"summary": "checkout-svc and inventory-svc are the services currently emitting '
    'logs.", "severity": "info", "confidence": "high", "root_cause": null, '
    '"affected_services": ["checkout-svc", "inventory-svc"], "hypotheses": [], '
    '"evidence": ["list_services returned checkout-svc and inventory-svc"], '
    '"recommended_actions": ["No action needed — informational query."]}'
)


def _sys_text(content) -> str:
    """Flatten a system message's content (str or Anthropic cache blocks) to text."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return " ".join(b.get("text", "") if isinstance(b, dict) else str(b) for b in content)
    return str(content)


class _ScriptedModel:
    """A deterministic stand-in for the chat model: picks a canned response by the
    node's system prompt (and, for the agent, by whether a tool result is present)."""

    def __init__(self, tool_names: list[str] | None = None):
        self._tool_names = tool_names or []

    def bind_tools(self, tools, **_kw):
        return _ScriptedModel([getattr(t, "name", str(t)) for t in tools])

    def invoke(self, messages, *_a, **_k):
        msgs = list(messages) if isinstance(messages, (list, tuple)) else [messages]
        sys_text = _sys_text(msgs[0].content) if msgs else ""
        last = msgs[-1]

        if "planning module" in sys_text:
            return AIMessage(content="1. List the services currently emitting logs.\n"
                                     "2. Summarize which services are active.")
        if "reflection module" in sys_text:
            return AIMessage(content="DONE")
        if "reporting module" in sys_text:
            return AIMessage(content=_REPORT_JSON)
        # Agent node: call the tool once, then answer from its result.
        if isinstance(last, ToolMessage):
            return AIMessage(content="The services currently emitting logs are "
                                     "checkout-svc and inventory-svc.")
        return AIMessage(content="", tool_calls=[
            {"name": "list_services", "args": {}, "id": "call_list_services"}])


def _fake_get_llm(fast: bool = False, model: str | None = None):
    """Drop-in for app.graph.nodes.get_llm: a scripted model wrapped for RECORD, keyed
    with the SAME model id the real resolver would use so replay keys match exactly."""
    model_id = model or llm_mod._resolve_model(fast)
    return replay.wrap(_ScriptedModel(), model_id)


def main() -> int:
    nodes.get_llm = _fake_get_llm  # graph nodes resolve get_llm from this module
    from evals.run_evals import _run_case

    replay.reset_cassette()
    cases = yaml.safe_load(Path("evals/golden_cases.yaml").read_text())
    passed = 0
    for case in cases:
        result = asyncio.run(_run_case(case))
        ok = result["passed"]
        passed += ok
        print(f"  {'✓' if ok else '✗'} {case['name']}: "
              f"kw={result['keyword_recall']:.0%} tools={result['tools_ok']} report={result['report_ok']}")
    entries = len(replay._cassette())
    print(f"\nRecorded {entries} cassette entr{'y' if entries == 1 else 'ies'} "
          f"→ {os.environ['COPILOT_CASSETTE_PATH']}")
    print(f"{passed}/{len(cases)} golden case(s) pass under the scripted agent.")
    return 0 if passed == len(cases) else 1


if __name__ == "__main__":
    raise SystemExit(main())
