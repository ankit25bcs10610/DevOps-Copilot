"""Rate limiter — fixed-window counting (in-memory) + Redis backend (fake client)."""

import asyncio

from app.ratelimit import InMemoryRateLimiter, RedisRateLimiter, make_rate_limiter


def test_in_memory_fixed_window():
    now = [1000.0]
    rl = InMemoryRateLimiter(clock=lambda: now[0])

    async def run():
        # limit=3: first 3 allowed, 4th over.
        assert [await rl.over_limit("ip", 3) for _ in range(4)] == [False, False, False, True]
        # window rolls over after 60s → counter resets.
        now[0] += 61
        assert await rl.over_limit("ip", 3) is False

    asyncio.run(run())


def test_in_memory_keys_are_independent():
    rl = InMemoryRateLimiter(clock=lambda: 0.0)

    async def run():
        assert await rl.over_limit("a", 1) is False
        assert await rl.over_limit("a", 1) is True
        assert await rl.over_limit("b", 1) is False  # different key unaffected

    asyncio.run(run())


class _FakeRedis:
    """Minimal async redis stand-in: INCR + EXPIRE over an in-proc dict."""

    def __init__(self):
        self.store: dict[str, int] = {}
        self.expires: dict[str, int] = {}

    async def incr(self, key):
        self.store[key] = self.store.get(key, 0) + 1
        return self.store[key]

    async def expire(self, key, ttl):
        self.expires[key] = ttl


def test_redis_backend_shares_window_and_sets_ttl():
    now = [0.0]
    fake = _FakeRedis()
    rl = RedisRateLimiter(fake, clock=lambda: now[0])

    async def run():
        assert [await rl.over_limit("ip", 2) for _ in range(3)] == [False, False, True]
        # TTL set once, on the first hit of the window.
        assert list(fake.expires.values()) == [60]

    asyncio.run(run())


def test_redis_backend_fails_open_when_client_errors():
    class _Broken:
        async def incr(self, key):
            raise RuntimeError("redis down")

    rl = RedisRateLimiter(_Broken())

    async def run():
        # A limiter outage must never block requests (fail open).
        assert await rl.over_limit("ip", 1) is False

    asyncio.run(run())


def test_factory_defaults_to_in_memory_without_url():
    assert isinstance(make_rate_limiter("", store={}), InMemoryRateLimiter)
