"""Observability: structured JSON log shape + Sentry hook is a safe no-op off."""

import json
import logging

import pytest

from app import observability


def test_setup_sentry_is_noop_without_dsn():
    # No SENTRY_DSN configured -> returns cleanly without importing/raising.
    observability.setup_sentry()


def test_setup_datadog_apm_is_noop_when_disabled(monkeypatch):
    # Neither APM nor LLMObs requested -> returns cleanly without instrumenting.
    monkeypatch.delenv("DD_LLMOBS_ENABLED", raising=False)
    observability.setup_datadog_apm()


def test_llmobs_import_path_is_valid():
    # ddtrace is the optional `apm` extra (not installed in base CI) — skip if absent.
    pytest.importorskip("ddtrace.llmobs")
    from ddtrace.llmobs import LLMObs

    assert hasattr(LLMObs, "enable")


def test_json_formatter_includes_request_id_and_level():
    record = logging.LogRecord("devcopilot", logging.INFO, __file__, 1, "hello", None, None)
    record.request_id = "rid-123"
    out = json.loads(observability._JsonFormatter().format(record))
    assert out["msg"] == "hello"
    assert out["level"] == "INFO"
    assert out["request_id"] == "rid-123"
