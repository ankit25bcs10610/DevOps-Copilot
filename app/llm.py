"""LLM factory. Centralizes model + provider selection in one place.

Provider is chosen via COPILOT_PROVIDER:
  - "anthropic" (default): Claude — Opus 4.8 for reasoning, Haiku 4.5 for the
    cheap plan/reflect nodes. Note: Opus 4.8 rejects `temperature`/`top_p`/`top_k`
    (HTTP 400), so we never send sampling params on the Anthropic path.
  - "groq": open models via Groq — Llama 3.3 70B + Llama 3.1 8B (fast, supports
    sampling params).
"""

from __future__ import annotations

from langchain_core.language_models.chat_models import BaseChatModel

from app.config import get_settings

# Per-provider default models: (main, fast).
_DEFAULTS = {
    "anthropic": {"main": "claude-opus-4-8", "fast": "claude-haiku-4-5"},
    "groq": {"main": "llama-3.3-70b-versatile", "fast": "llama-3.1-8b-instant"},
}


def get_llm(fast: bool = False, model: str | None = None) -> BaseChatModel:
    """Return a configured chat model.

    Args:
        fast: use the cheaper model (for plan/reflect) instead of the main one.
        model: explicit model id override (skips the provider default).
    """
    settings = get_settings()
    provider = settings.copilot_provider.lower()
    if provider not in _DEFAULTS:
        raise ValueError(f"unknown COPILOT_PROVIDER '{provider}' (use anthropic|groq)")

    if model is None:
        override = settings.copilot_fast_model if fast else settings.copilot_model
        model = override or _DEFAULTS[provider]["fast" if fast else "main"]

    if provider == "anthropic":
        from langchain_anthropic import ChatAnthropic

        # No temperature: Opus 4.8 removed sampling params and 400s if they're sent.
        return ChatAnthropic(
            model=model,
            api_key=settings.anthropic_api_key,
            max_tokens=4096,
        )

    from langchain_groq import ChatGroq

    return ChatGroq(
        model=model,
        api_key=settings.groq_api_key,
        temperature=0.0,
        max_tokens=4096,
    )


def resolved_models() -> tuple[str, str]:
    """Return the (main, fast) model ids that will actually be used — with
    provider defaults applied. Construction makes no API call."""
    return get_llm().model, get_llm(fast=True).model


def active_api_key() -> str:
    """The API key required for the configured provider (for startup checks)."""
    settings = get_settings()
    return (
        settings.anthropic_api_key
        if settings.copilot_provider.lower() == "anthropic"
        else settings.groq_api_key
    )
