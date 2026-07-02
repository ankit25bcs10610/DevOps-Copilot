"""Fleet-wide LLM spend cap — a hard ceiling on total tokens across the deployment.

Per-run (COPILOT_MAX_TOKENS_PER_RUN) and per-tenant (plan quota) budgets already
exist; this adds the missing GLOBAL guardrail so a bug or abuse spike can't run up an
unbounded bill across the whole fleet. When the rolling window's tokens cross the cap,
new investigations are refused until the window rolls over.

Two backends behind one interface (mirrors app/ratelimit.py):
  - InMemorySpend: per-process counter (single-instance).
  - RedisSpend: INCRBY on a windowed key, shared across replicas (production).

The cap/alert math is pure and unit-tested; the Redis client is injected.
"""

from __future__ import annotations

import logging
import time
from typing import Any, Callable

log = logging.getLogger("devcopilot.spend")


def over_cap(total: int, cap: int) -> bool:
    """True when the rolling total has reached the cap (0 = no cap)."""
    return cap > 0 and total >= cap


def alert_due(total: int, cap: int, fraction: float) -> bool:
    """True when spend has crossed the soft alert threshold (but not necessarily the cap)."""
    return cap > 0 and total >= cap * fraction


class InMemorySpend:
    """Per-process token counter over a fixed rolling window."""

    def __init__(self, window_s: int = 86_400, clock: Callable[[], float] = time.time):
        self._window = window_s
        self._clock = clock
        self._start = clock()
        self._total = 0

    def _roll(self) -> None:
        if self._clock() - self._start >= self._window:
            self._start, self._total = self._clock(), 0

    async def total(self) -> int:
        self._roll()
        return self._total

    async def record(self, tokens: int) -> int:
        self._roll()
        self._total += max(0, int(tokens or 0))
        return self._total


class RedisSpend:
    """Shared token counter across replicas: INCRBY on a window-bucketed key with TTL."""

    def __init__(self, client: Any, window_s: int = 86_400, clock: Callable[[], float] = time.time):
        self._redis = client
        self._window = window_s
        self._clock = clock

    def _key(self) -> str:
        return f"spend:{int(self._clock() // self._window)}"

    async def total(self) -> int:
        try:
            val = await self._redis.get(self._key())
            return int(val or 0)
        except Exception:  # noqa: BLE001
            log.warning("redis spend read failed", exc_info=True)
            return 0

    async def record(self, tokens: int) -> int:
        key = self._key()
        try:
            total = await self._redis.incrby(key, max(0, int(tokens or 0)))
            if total == max(0, int(tokens or 0)):  # first write in this window
                await self._redis.expire(key, self._window)
            return int(total)
        except Exception:  # noqa: BLE001 — never fail a request because metering is down
            log.warning("redis spend record failed", exc_info=True)
            return 0


def make_spend_tracker(redis_url: str, window_s: int = 86_400) -> Any:
    if redis_url.strip():
        try:
            import redis.asyncio as aioredis

            return RedisSpend(aioredis.from_url(redis_url, decode_responses=True), window_s)
        except Exception:  # noqa: BLE001
            log.warning("redis spend tracker unavailable; using in-memory", exc_info=True)
    return InMemorySpend(window_s)
