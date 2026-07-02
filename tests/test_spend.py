"""Fleet-wide spend cap — cap/alert math + in-memory & Redis trackers."""

import asyncio

from app.spend import InMemorySpend, RedisSpend, alert_due, make_spend_tracker, over_cap


def test_over_cap_and_alert_math():
    assert over_cap(100, 100) is True
    assert over_cap(99, 100) is False
    assert over_cap(10, 0) is False           # 0 = no cap
    assert alert_due(80, 100, 0.8) is True
    assert alert_due(79, 100, 0.8) is False
    assert alert_due(80, 0, 0.8) is False     # no cap -> no alert


def test_in_memory_accumulates_and_rolls_window():
    now = [1000.0]
    s = InMemorySpend(window_s=3600, clock=lambda: now[0])

    async def run():
        assert await s.record(500) == 500
        assert await s.record(300) == 800
        assert await s.total() == 800
        now[0] += 3601                         # window rolls over -> reset
        assert await s.total() == 0

    asyncio.run(run())


def test_redis_spend_incrby_and_ttl():
    now = [0.0]

    class _FakeRedis:
        def __init__(self):
            self.store: dict[str, int] = {}
            self.expires: dict[str, int] = {}

        async def incrby(self, key, amount):
            self.store[key] = self.store.get(key, 0) + amount
            return self.store[key]

        async def get(self, key):
            return self.store.get(key)

        async def expire(self, key, ttl):
            self.expires[key] = ttl

    fake = _FakeRedis()
    s = RedisSpend(fake, window_s=86_400, clock=lambda: now[0])

    async def run():
        assert await s.record(1000) == 1000
        assert await s.record(500) == 1500
        assert await s.total() == 1500
        assert list(fake.expires.values()) == [86_400]  # TTL set once

    asyncio.run(run())


def test_redis_spend_fails_open_on_error():
    class _Broken:
        async def get(self, key):
            raise RuntimeError("down")

        async def incrby(self, key, amount):
            raise RuntimeError("down")

    s = RedisSpend(_Broken())

    async def run():
        assert await s.total() == 0        # read fails -> 0 (don't block)
        assert await s.record(100) == 0    # write fails -> 0

    asyncio.run(run())


def test_factory_defaults_in_memory():
    assert isinstance(make_spend_tracker(""), InMemorySpend)
