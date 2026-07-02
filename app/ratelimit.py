"""Rate limiting — a fixed-window limiter with pluggable backends.

The API's per-IP POST limit must hold across replicas, but a single process is fine
for local/dev. This provides one interface with two backends:

  - InMemoryRateLimiter: per-process dict (default; single-instance only).
  - RedisRateLimiter: atomic INCR + EXPIRE, so the window is shared across every
    replica (the production choice). Selected when COPILOT_REDIS_URL is set.

The counting logic is deterministic (injectable clock) and unit-tested; the Redis
client is injected so the backend tests need no live server.
"""

from __future__ import annotations

import logging
import time
from typing import Any, Callable

log = logging.getLogger("devcopilot.ratelimit")


class InMemoryRateLimiter:
    """Per-process fixed-window counter. `store` maps key -> (window_start, count);
    passing the module's dict in keeps it inspectable/clearable (tests)."""

    def __init__(self, store: dict[str, tuple[float, int]] | None = None,
                 clock: Callable[[], float] = time.time, max_keys: int = 10_000):
        self._store = store if store is not None else {}
        self._clock = clock
        self._max_keys = max_keys

    async def over_limit(self, key: str, limit: int, window: int = 60) -> bool:
        now = self._clock()
        if len(self._store) > self._max_keys:  # bound memory: drop elapsed windows
            for k in [k for k, (start, _) in self._store.items() if now - start >= window]:
                del self._store[k]
        start, count = self._store.get(key, (now, 0))
        if now - start >= window:
            start, count = now, 0
        count += 1
        self._store[key] = (start, count)
        return count > limit


class RedisRateLimiter:
    """Shared fixed-window counter across replicas via atomic INCR + EXPIRE. The
    first hit in a window sets the TTL; every replica increments the same key."""

    def __init__(self, client: Any, clock: Callable[[], float] = time.time):
        self._redis = client
        self._clock = clock

    async def over_limit(self, key: str, limit: int, window: int = 60) -> bool:
        # Bucket by window so keys expire on their own; INCR is atomic across replicas.
        bucket = int(self._clock() // window)
        rkey = f"rl:{key}:{bucket}"
        try:
            count = await self._redis.incr(rkey)
            if count == 1:
                await self._redis.expire(rkey, window)
            return count > limit
        except Exception:  # noqa: BLE001 — never fail a request because the limiter is down
            log.warning("redis rate-limiter unavailable; allowing request", exc_info=True)
            return False


def make_rate_limiter(redis_url: str, store: dict | None = None) -> Any:
    """Build the configured limiter: Redis when a URL is set, else in-memory. Falls
    back to in-memory if the redis package/connection can't be established."""
    if redis_url.strip():
        try:
            import redis.asyncio as aioredis  # optional dependency

            client = aioredis.from_url(redis_url, encoding="utf-8", decode_responses=True)
            log.info("rate limiter: redis (%s)", redis_url.split("@")[-1])
            return RedisRateLimiter(client)
        except Exception:  # noqa: BLE001 — degrade gracefully to in-memory
            log.warning("redis rate limiter unavailable; using in-memory", exc_info=True)
    return InMemoryRateLimiter(store)
