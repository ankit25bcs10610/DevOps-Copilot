"""Model resolution across providers (pure — no API calls)."""

import pytest

import app.config as cfg
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


# --- prompt caching: cached_system() --------------------------------------- #
# The Anthropic path emits cache_control content blocks; every other provider and
# the disabled path must return a plain string byte-identical to the old
# `SystemMessage(content=base + "\n\n" + feedback)` so caching can't drift behavior.
def test_cached_system_anthropic_emits_cache_control(monkeypatch):
    monkeypatch.setenv("COPILOT_PROMPT_CACHE", "true")
    cfg.get_settings.cache_clear()
    runtime.reset()
    runtime.set_model("anthropic", "k", "", "")
    try:
        msg = llm.cached_system("STABLE PREFIX", "VOLATILE FEEDBACK")
        assert isinstance(msg.content, list)
        assert msg.content[0]["text"] == "STABLE PREFIX"
        assert msg.content[0]["cache_control"] == {"type": "ephemeral"}
        assert msg.content[1]["text"] == "VOLATILE FEEDBACK"
        assert "cache_control" not in msg.content[1]  # volatile stays after the breakpoint
        only = llm.cached_system("STABLE PREFIX")
        assert len(only.content) == 1 and only.content[0]["cache_control"]
    finally:
        runtime.reset()
        cfg.get_settings.cache_clear()


def test_cached_system_other_provider_is_plain_string(monkeypatch):
    monkeypatch.setenv("COPILOT_PROMPT_CACHE", "true")
    cfg.get_settings.cache_clear()
    runtime.reset()
    runtime.set_model("openai", "k", "", "")
    try:
        assert llm.cached_system("S", "V").content == "S\n\nV"
        assert llm.cached_system("S").content == "S"
    finally:
        runtime.reset()
        cfg.get_settings.cache_clear()


def test_cached_system_disabled_is_plain_string_even_on_anthropic(monkeypatch):
    monkeypatch.setenv("COPILOT_PROMPT_CACHE", "false")
    cfg.get_settings.cache_clear()
    runtime.reset()
    runtime.set_model("anthropic", "k", "", "")
    try:
        assert llm.cached_system("S", "V").content == "S\n\nV"
        assert llm.cached_system("S").content == "S"
    finally:
        runtime.reset()
        cfg.get_settings.cache_clear()
