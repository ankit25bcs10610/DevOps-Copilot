"""Durable job queue — enqueue/dequeue, retry, and dead-letter."""

import asyncio

from app.jobqueue import InMemoryJobQueue, Job, make_job_queue, process_one


async def _noop_sleep(_s):
    return None


def test_in_memory_enqueue_dequeue_fifo():
    q = InMemoryJobQueue()

    async def run():
        await q.enqueue(Job(kind="investigate", payload={"id": "a"}))
        await q.enqueue(Job(kind="investigate", payload={"id": "b"}))
        assert (await q.dequeue()).payload["id"] == "a"
        assert (await q.dequeue()).payload["id"] == "b"

    asyncio.run(run())


def test_process_one_success():
    q = InMemoryJobQueue()
    handled: list[str] = []

    async def handler(job):
        handled.append(job.payload["id"])

    async def run():
        await q.enqueue(Job(kind="investigate", payload={"id": "x"}))
        outcome = await process_one(q, handler, sleep=_noop_sleep)
        assert outcome == "done"
        assert handled == ["x"]

    asyncio.run(run())


def test_process_one_retries_then_dead_letters():
    q = InMemoryJobQueue()

    async def always_fails(_job):
        raise RuntimeError("boom")

    async def run():
        await q.enqueue(Job(kind="investigate", payload={"id": "z"}))
        # attempt 1 -> retried (re-enqueued), attempt 2 -> retried, attempt 3 -> dead
        assert await process_one(q, always_fails, max_attempts=3, sleep=_noop_sleep) == "retried"
        assert await process_one(q, always_fails, max_attempts=3, sleep=_noop_sleep) == "retried"
        assert await process_one(q, always_fails, max_attempts=3, sleep=_noop_sleep) == "dead"
        assert len(q.dead_letter) == 1
        assert q.dead_letter[0].attempts == 3

    asyncio.run(run())


def test_process_one_succeeds_on_retry():
    q = InMemoryJobQueue()
    calls = {"n": 0}

    async def flaky(_job):
        calls["n"] += 1
        if calls["n"] < 2:
            raise RuntimeError("transient")

    async def run():
        await q.enqueue(Job(kind="investigate", payload={"id": "r"}))
        assert await process_one(q, flaky, sleep=_noop_sleep) == "retried"
        assert await process_one(q, flaky, sleep=_noop_sleep) == "done"
        assert q.dead_letter == []

    asyncio.run(run())


def test_factory_defaults_to_in_memory():
    assert isinstance(make_job_queue(""), InMemoryJobQueue)


def test_redis_job_queue_roundtrip_with_fake():
    from app.jobqueue import RedisJobQueue

    class _FakeRedis:
        def __init__(self):
            self.lists: dict[str, list[str]] = {}

        async def lpush(self, key, val):
            self.lists.setdefault(key, []).insert(0, val)

        async def brpop(self, key):
            return (key, self.lists[key].pop())

    fake = _FakeRedis()
    q = RedisJobQueue(fake, key="k")

    async def run():
        await q.enqueue(Job(kind="investigate", payload={"id": "1"}, id="1"))
        job = await q.dequeue()
        assert job.kind == "investigate" and job.payload["id"] == "1"
        await q.to_dead_letter(job)
        assert fake.lists["k:dead"]

    asyncio.run(run())
