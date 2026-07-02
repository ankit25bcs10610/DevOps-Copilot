"""Checkpointer routing — the multi-instance seam (SQLite default / Postgres URL).

Graph state is keyed by thread_id and lives in the checkpointer, so an evicted
session rehydrates on any replica that shares the store. This tests that
make_checkpointer picks the right backend by URL."""

import asyncio

import pytest

import app.config as cfg
from app.graph import builder


def test_defaults_to_sqlite_saver(monkeypatch):
    monkeypatch.setenv("COPILOT_CHECKPOINT_DB", "./x.sqlite")
    cfg.get_settings.cache_clear()
    cm = builder.make_checkpointer()
    assert hasattr(cm, "__aenter__")  # an async context manager (SQLite saver)
    cfg.get_settings.cache_clear()


def test_postgres_url_routes_to_postgres_checkpointer(monkeypatch):
    monkeypatch.setenv("COPILOT_CHECKPOINT_DB", "postgresql://u@h/db")
    cfg.get_settings.cache_clear()
    cm = builder.make_checkpointer()
    try:
        import langgraph.checkpoint.postgres.aio  # noqa: F401
        pytest.skip("postgres checkpointer package present; can't assert the offline error")
    except ImportError:
        pass

    async def _enter():
        async with cm:
            pass

    # Routed to the Postgres branch → clear install-hint error when the pkg is absent.
    with pytest.raises(RuntimeError, match="langgraph-checkpoint-postgres"):
        asyncio.run(_enter())
    cfg.get_settings.cache_clear()
