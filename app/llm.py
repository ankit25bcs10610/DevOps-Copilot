"""LLM factory. Centralizes model selection so we can swap models or tiers
(e.g. Opus for hard reasoning, Sonnet for cost/speed) in one place."""

from __future__ import annotations

from langchain_anthropic import ChatAnthropic

from app.config import get_settings


def get_llm(temperature: float = 0.0, model: str | None = None) -> ChatAnthropic:
    """Return a configured Claude chat model.

    temperature defaults to 0 for deterministic, reproducible agent behavior.
    """
    settings = get_settings()
    return ChatAnthropic(
        model=model or settings.copilot_model,
        api_key=settings.anthropic_api_key,
        temperature=temperature,
        max_tokens=4096,
    )
