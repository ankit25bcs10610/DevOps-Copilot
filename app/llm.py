"""LLM factory. Centralizes model + provider selection in one place.

Provider is chosen via COPILOT_PROVIDER (or the UI), one of:
  - "anthropic" (default): Claude — Opus 4.8 for reasoning, Haiku 4.5 for the
    cheap plan/reflect nodes. Note: Opus 4.8 rejects `temperature`/`top_p`/`top_k`
    (HTTP 400), so we never send sampling params on the Anthropic path.
  - "openai":   GPT-4o / GPT-4o-mini        (needs `langchain-openai`)
  - "gemini":   Google Gemini 1.5 Pro/Flash (needs `langchain-google-genai`)
  - "groq":     Llama 3.3 70B / 3.1 8B       (needs `langchain-groq`)
  - "deepseek": DeepSeek Chat (OpenAI-compatible, needs `langchain-openai`)

The openai/gemini/deepseek SDKs are optional — imported lazily so the server
always starts; selecting a provider whose SDK isn't installed raises a clear
error the UI surfaces.
"""

from __future__ import annotations

import importlib

from langchain_core.language_models.chat_models import BaseChatModel

from app import runtime

# Per-provider default models: (main, fast).
_DEFAULTS = {
    "anthropic": {"main": "claude-opus-4-8", "fast": "claude-haiku-4-5"},
    "openai": {"main": "gpt-4o", "fast": "gpt-4o-mini"},
    "gemini": {"main": "gemini-1.5-pro", "fast": "gemini-1.5-flash"},
    "groq": {"main": "llama-3.3-70b-versatile", "fast": "llama-3.1-8b-instant"},
    "deepseek": {"main": "deepseek-chat", "fast": "deepseek-chat"},
}

# DeepSeek speaks the OpenAI wire protocol — reuse ChatOpenAI against this base URL.
_DEEPSEEK_BASE_URL = "https://api.deepseek.com"


def _resolve_model(fast: bool) -> str:
    """Resolve the model id for the active provider (pure; no API call)."""
    provider = runtime.provider()
    if provider not in _DEFAULTS:
        raise ValueError(f"unknown provider '{provider}' (use {'|'.join(_DEFAULTS)})")
    override = runtime.fast_model_override() if fast else runtime.model_override()
    return override or _DEFAULTS[provider]["fast" if fast else "main"]


def _require(module: str, label: str):
    """Import an optional provider SDK, raising a UI-friendly error if it's missing."""
    try:
        return importlib.import_module(module)
    except ImportError as exc:  # noqa: TRY003
        pkg = module.replace("_", "-")
        raise RuntimeError(
            f"{label} support needs the '{pkg}' package. "
            f"Install it (e.g. `uv pip install {pkg}`) and retry."
        ) from exc


def get_llm(fast: bool = False, model: str | None = None) -> BaseChatModel:
    """Return a configured chat model for the active provider (runtime override
    aware, so the UI can switch provider/model/key without a restart).

    Args:
        fast: use the cheaper model (for plan/reflect) instead of the main one.
        model: explicit model id override (skips the provider default).
    """
    provider = runtime.provider()
    if model is None:
        model = _resolve_model(fast)
    key = runtime.provider_key()

    if provider == "anthropic":
        from langchain_anthropic import ChatAnthropic

        # No temperature: Opus 4.8 removed sampling params and 400s if they're sent.
        params: dict = {"model": model, "api_key": key, "max_tokens": 4096}
        if not fast:
            # Adaptive thinking on the MAIN reasoning model only — Claude decides
            # when and how much to reason (and interleaves it between tool calls).
            # Kept off the cheap fast plan/reflect model to hold latency + cost
            # down. The agent binds tools with tool_choice="auto" (graph/nodes.py),
            # which thinking requires — forced tool use + thinking is a 400.
            params["thinking"] = {"type": "adaptive"}
            # Thinking + answer share the budget, so give the main model headroom.
            params["max_tokens"] = 8192
        return ChatAnthropic(**params)

    if provider == "groq":
        from langchain_groq import ChatGroq

        return ChatGroq(model=model, api_key=key, temperature=0.0, max_tokens=4096)

    if provider == "openai":
        mod = _require("langchain_openai", "OpenAI")
        return mod.ChatOpenAI(model=model, api_key=key, temperature=0.0, max_tokens=4096)

    if provider == "deepseek":
        mod = _require("langchain_openai", "DeepSeek")
        return mod.ChatOpenAI(
            model=model,
            api_key=key,
            base_url=_DEEPSEEK_BASE_URL,
            temperature=0.0,
            max_tokens=4096,
        )

    if provider == "gemini":
        mod = _require("langchain_google_genai", "Gemini")
        return mod.ChatGoogleGenerativeAI(
            model=model, google_api_key=key, temperature=0.0, max_output_tokens=4096
        )

    raise ValueError(f"unknown provider '{provider}' (use {'|'.join(_DEFAULTS)})")


def resolved_models() -> tuple[str, str]:
    """Return the (main, fast) model ids that will be used. Pure — builds no
    client (the old version constructed two chat models just to read .model)."""
    return _resolve_model(False), _resolve_model(True)


def active_api_key() -> str:
    """The API key required for the active provider (for startup checks)."""
    return runtime.provider_key()
