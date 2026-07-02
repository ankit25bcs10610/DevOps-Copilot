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
import logging

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import SystemMessage

from app import replay, runtime
from app.config import get_settings

log = logging.getLogger("devcopilot.llm")

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

    # Replay mode serves recorded responses with no real client (and no key), so
    # skip building the provider SDK entirely.
    if replay.mode() == "replay":
        return replay.replay_model(model)

    key = runtime.provider_key()
    # Build the real client, wrap it for resilience (retries + breaker + optional
    # cross-provider failover), then for record. Resilience sits INSIDE the record
    # wrapper so the cassette captures the final successful response. Replay never
    # reaches here (early return above), so offline/deterministic paths are unaffected.
    built = _build(provider, model, key, fast)
    return replay.wrap(_resilient(built, model, fast), model)


class _ResilientLLM:
    """Wrap a chat model's `.invoke` with bounded retry + a circuit breaker, and
    optional cross-provider failover. Passes `bind_tools`/`with_structured_output`
    through, re-wrapping so the whole chain stays resilient. Only built for the real
    (non-replay) path, so it never interferes with deterministic replay."""

    def __init__(self, inner: BaseChatModel, model_id: str, fast: bool,
                 breaker: "object | None" = None):
        from app.resilience import CircuitBreaker

        self._inner = inner
        self._model_id = model_id
        self._fast = fast
        # One breaker per (model) wrapper instance; shared across bind_tools children.
        self._breaker = breaker or CircuitBreaker(failure_threshold=5, reset_timeout=30.0)

    def bind_tools(self, tools, **kwargs):
        return _ResilientLLM(self._inner.bind_tools(tools, **kwargs), self._model_id,
                             self._fast, self._breaker)

    def invoke(self, messages, *args, **kwargs):
        from app import resilience
        from app.config import get_settings

        settings = get_settings()
        attempts = max(1, settings.copilot_llm_retries)

        def _primary():
            return self._breaker.call(lambda: self._inner.invoke(messages, *args, **kwargs))

        try:
            return resilience.retry_call(_primary, attempts=attempts)
        except BaseException as exc:  # noqa: BLE001 — try failover before giving up
            fb = settings.copilot_fallback_provider.strip().lower()
            if fb and fb != runtime.provider() and resilience.is_retryable(exc):
                log.warning("primary provider failing; failing over to '%s'", fb)
                fallback = _build_fallback(fb, self._model_id, self._fast, getattr(self._inner, "_tools", None))
                if fallback is not None:
                    return fallback.invoke(messages, *args, **kwargs)
            raise

    def __getattr__(self, name):
        inner = self.__dict__.get("_inner")
        if inner is None:
            raise AttributeError(name)
        return getattr(inner, name)


def _resilient(model: BaseChatModel, model_id: str, fast: bool) -> BaseChatModel:
    """Wrap a built model with resilience unless retries are disabled (retries==1
    and no failover configured), in which case return it untouched."""
    settings = get_settings()
    if settings.copilot_llm_retries <= 1 and not settings.copilot_fallback_provider.strip():
        return model
    return _ResilientLLM(model, model_id, fast)  # type: ignore[return-value]


def _build_fallback(provider: str, model_id: str, fast: bool, tools):
    """Build a model on the fallback provider (its own default model + key), best-effort."""
    if provider not in _DEFAULTS:
        return None
    try:
        key = runtime.provider_key(provider)
        model = _DEFAULTS[provider]["fast" if fast else "main"]
        built = _build(provider, model, key, fast)
        return built.bind_tools(tools) if tools else built
    except Exception:  # noqa: BLE001 — failover is best-effort; original error will surface
        log.warning("failover provider '%s' unavailable", provider, exc_info=True)
        return None


def _build(provider: str, model: str, key: str, fast: bool) -> BaseChatModel:
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
        from pydantic import SecretStr

        return ChatGroq(model=model, api_key=SecretStr(key), temperature=0.0, max_tokens=4096)

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


def cached_system(stable: str, volatile: str = "") -> SystemMessage:
    """Build the agent's system message with a cache breakpoint after the stable
    prefix, so Anthropic caches the (tools + system) prefix across the many agent
    loop iterations in one investigation — they re-read it at ~0.1x input cost.

    `stable` is the unchanging prefix (the agent system prompt + plan, constant
    within a run); `volatile` is per-iteration text (reviewer feedback, cap notice)
    placed AFTER the breakpoint so it never invalidates the cached prefix.

    Provider-neutral: only the Anthropic path emits cache_control content blocks.
    Every other provider (and the cache-disabled path) gets a plain string, so
    behavior is byte-identical to before — caching is purely additive.
    """
    enabled = get_settings().copilot_prompt_cache and runtime.provider() == "anthropic"
    if not enabled:
        return SystemMessage(content=stable + (("\n\n" + volatile) if volatile else ""))
    blocks: list[str | dict] = [
        {"type": "text", "text": stable, "cache_control": {"type": "ephemeral"}}
    ]
    if volatile:
        blocks.append({"type": "text", "text": volatile})
    return SystemMessage(content=blocks)


def resolved_models() -> tuple[str, str]:
    """Return the (main, fast) model ids that will be used. Pure — builds no
    client (the old version constructed two chat models just to read .model)."""
    return _resolve_model(False), _resolve_model(True)


def active_api_key() -> str:
    """The API key required for the active provider (for startup checks)."""
    return runtime.provider_key()
