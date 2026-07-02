"""Prompt registry + benchmark-gated A/B comparison."""

import json

from app.prompt_registry import PromptRegistry, ab_gate


def test_registry_seeds_from_prompts():
    r = PromptRegistry()
    assert r.versions("AGENT_SYSTEM") == ["v1"]
    assert r.active_version("AGENT_SYSTEM") == "v1"
    assert r.get("AGENT_SYSTEM")  # non-empty active text


def test_register_and_activate_version():
    r = PromptRegistry(seed={"AGENT_SYSTEM": {"v1": "base"}})
    r.register("AGENT_SYSTEM", "v2", "candidate")
    assert set(r.versions("AGENT_SYSTEM")) == {"v1", "v2"}
    assert r.get("AGENT_SYSTEM") == "base"
    assert r.set_active("AGENT_SYSTEM", "v2") is True
    assert r.get("AGENT_SYSTEM") == "candidate"
    assert r.set_active("AGENT_SYSTEM", "v9") is False  # unknown version


def test_load_overrides(tmp_path):
    r = PromptRegistry(seed={"AGENT_SYSTEM": {"v1": "base"}})
    f = tmp_path / "overrides.json"
    f.write_text(json.dumps({"AGENT_SYSTEM": {"v2": "from-file"}}))
    assert r.load_overrides(str(f)) == 1
    assert "v2" in r.versions("AGENT_SYSTEM")
    assert r.load_overrides(str(tmp_path / "missing.json")) == 0  # best-effort


def test_ab_gate_blocks_regression():
    base = {"overall": {"a1": 0.8, "pcw": 0.8, "loc_top1": 0.9, "groundedness": 1.0}}
    worse = {"overall": {"a1": 0.6, "pcw": 0.7, "loc_top1": 0.9, "groundedness": 1.0}}
    out = ab_gate(base, worse)
    assert out["regressed"] is True
    assert "a1" in out["regressions"]
    assert out["verdict"] == "regressed"


def test_ab_gate_passes_improvement_and_neutral():
    base = {"overall": {"a1": 0.7, "pcw": 0.7, "loc_top1": 0.8, "groundedness": 1.0}}
    better = {"overall": {"a1": 0.9, "pcw": 0.8, "loc_top1": 0.8, "groundedness": 1.0}}
    assert ab_gate(base, better)["verdict"] == "improved"
    assert ab_gate(base, better)["regressed"] is False
    # within tolerance -> neutral, not a regression
    neutral = {"overall": {"a1": 0.69, "pcw": 0.70, "loc_top1": 0.80, "groundedness": 1.0}}
    assert ab_gate(base, neutral)["regressed"] is False


def test_ab_gate_accepts_bare_overall_dicts():
    assert ab_gate({"a1": 0.5}, {"a1": 0.2})["regressed"] is True
