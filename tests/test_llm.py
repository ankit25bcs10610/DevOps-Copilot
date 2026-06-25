"""Model resolution across providers (pure — no API calls)."""

import pytest

from app import llm, runtime


def test_provider_defaults_resolve():
    runtime.reset()
    runtime.set_model("openai", "k", "", "")
    assert llm._resolve_model(False) == "gpt-4o"
    assert llm._resolve_model(True) == "gpt-4o-mini"
    runtime.reset()


def test_explicit_model_override_wins():
    runtime.reset()
    runtime.set_model("anthropic", "k", "claude-sonnet-4-6", "")
    assert llm._resolve_model(False) == "claude-sonnet-4-6"
    runtime.reset()


def test_all_providers_have_defaults():
    for p in ("anthropic", "openai", "gemini", "groq", "deepseek"):
        assert "main" in llm._DEFAULTS[p] and "fast" in llm._DEFAULTS[p]


def test_unknown_provider_raises():
    runtime.reset()
    runtime.set_model("bogus", "k", "", "")
    with pytest.raises(ValueError):
        llm._resolve_model(False)
    runtime.reset()
