"""LLM factory. Centralizes model selection so we can swap models or providers
in one place. Backed by Groq (fast, OpenAI-compatible inference of open models
like Llama 3.3 70B, which supports tool calling)."""

from __future__ import annotations

from langchain_groq import ChatGroq

from app.config import get_settings


def get_llm(temperature: float = 0.0, model: str | None = None) -> ChatGroq:
    """Return a configured Groq chat model.

    temperature defaults to 0 for deterministic, reproducible agent behavior.
    """
    settings = get_settings()
    return ChatGroq(
        model=model or settings.copilot_model,
        api_key=settings.groq_api_key,
        temperature=temperature,
        max_tokens=4096,
    )
