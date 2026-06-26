"""Logging + LangSmith tracing setup.

Call `init()` once at process startup (API and CLI) BEFORE any LLM or graph is
constructed, so the LangSmith env vars are in place when LangChain builds its
tracer. Previously these settings existed in config but were never applied, so
every run was untraced.

Logging emits structured JSON in production (machine-parseable for aggregators)
and a readable text line in development. Every record carries the active
`request_id` so an API request can be correlated end-to-end across the log.
"""

from __future__ import annotations

import contextvars
import json
import logging
import os

from app.config import get_settings

log = logging.getLogger("devcopilot")
_INITIALIZED = False

# Set by the API request-id middleware; flows into every log record via the
# filter below. Defaults to "-" for non-request contexts (CLI, startup).
request_id_var: contextvars.ContextVar[str] = contextvars.ContextVar("request_id", default="-")


class _RequestIdFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.request_id = request_id_var.get()
        return True


class _JsonFormatter(logging.Formatter):
    """One JSON object per line — ts, level, logger, request_id, msg, (+exc)."""

    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "ts": self.formatTime(record),
            "level": record.levelname,
            "logger": record.name,
            "request_id": getattr(record, "request_id", "-"),
            "msg": record.getMessage(),
        }
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str)


def configure_logging() -> None:
    """Install a single root handler. Honors LOG_LEVEL (default INFO) and
    LOG_FORMAT (json|text); defaults to json in production, text otherwise."""
    level = os.environ.get("LOG_LEVEL", "INFO").upper()
    default_fmt = "json" if get_settings().is_production else "text"
    fmt = os.environ.get("LOG_FORMAT", default_fmt).lower()

    handler = logging.StreamHandler()
    handler.addFilter(_RequestIdFilter())
    if fmt == "json":
        handler.setFormatter(_JsonFormatter())
    else:
        handler.setFormatter(
            logging.Formatter("%(asctime)s %(levelname)-5s %(name)s [%(request_id)s] :: %(message)s")
        )

    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(level)


def setup_langsmith() -> None:
    """Export LangSmith env vars when COPILOT tracing is enabled. Uses setdefault
    so an explicitly-set environment always wins over the .env-derived values."""
    s = get_settings()
    if not s.langchain_tracing_v2:
        return
    os.environ.setdefault("LANGCHAIN_TRACING_V2", "true")
    os.environ.setdefault("LANGSMITH_TRACING", "true")
    if s.langchain_api_key:
        os.environ.setdefault("LANGCHAIN_API_KEY", s.langchain_api_key)
        os.environ.setdefault("LANGSMITH_API_KEY", s.langchain_api_key)
    os.environ.setdefault("LANGCHAIN_PROJECT", s.langchain_project)
    os.environ.setdefault("LANGSMITH_PROJECT", s.langchain_project)
    log.info("LangSmith tracing enabled (project=%s)", s.langchain_project)


def setup_datadog_apm() -> None:
    """Enable Datadog APM self-instrumentation when DD_TRACE_ENABLED is set.

    This traces the copilot ITSELF (FastAPI requests, httpx calls, etc.) to a
    Datadog agent — distinct from the `datadog` MCP connector, which *reads* your
    services' logs/metrics. ddtrace honors DD_SERVICE / DD_ENV / DD_AGENT_HOST from
    the environment. No-op (with a warning) if disabled or ddtrace isn't installed."""
    if not get_settings().dd_trace_enabled:
        return
    os.environ.setdefault("DD_SERVICE", "devops-copilot")
    os.environ.setdefault("DD_ENV", get_settings().copilot_env)
    try:
        import ddtrace.auto  # noqa: F401  — auto-instruments imported libraries on import
    except ImportError:
        log.warning("DD_TRACE_ENABLED is set but ddtrace is not installed "
                    "(uv pip install ddtrace, or the 'apm' extra)")
        return
    log.info("Datadog APM enabled (service=%s env=%s)",
             os.environ.get("DD_SERVICE"), get_settings().copilot_env)


def setup_sentry() -> None:
    """Enable Sentry error tracking when SENTRY_DSN is set. Sentry's default
    logging integration captures our `log.exception(...)` calls as events, so no
    per-handler wiring is needed. No-op (with a warning) if the SDK isn't installed."""
    s = get_settings()
    if not s.sentry_dsn:
        return
    try:
        import sentry_sdk
    except ImportError:
        log.warning("SENTRY_DSN is set but sentry-sdk is not installed (pip install sentry-sdk)")
        return
    sentry_sdk.init(dsn=s.sentry_dsn, environment=s.copilot_env, traces_sample_rate=0.0)
    log.info("Sentry error tracking enabled (env=%s)", s.copilot_env)


def init() -> None:
    """Idempotent one-time setup."""
    global _INITIALIZED
    if _INITIALIZED:
        return
    _INITIALIZED = True
    configure_logging()
    setup_langsmith()
    setup_sentry()
    setup_datadog_apm()
