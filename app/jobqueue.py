"""Durable-ish job queue for background investigations — retries + dead-letter.

Triggered (PagerDuty webhook) and proactive (SLO poller) investigations were
fire-and-forget `asyncio.create_task` — a pod restart dropped them, and a transient
failure lost them silently. This adds a small queue with bounded retries and a
dead-letter list, behind one interface with two backends:

  - InMemoryJobQueue: an asyncio queue (default; drains on restart — fine for dev /
    single instance).
  - RedisJobQueue: a Redis list (BRPOP/LPUSH), so jobs survive a restart and can be
    processed by any replica (the production choice). Selected via COPILOT_REDIS_URL.

The retry/dead-letter orchestration lives in a backend-agnostic worker step that is
pure enough to unit-test with a fake queue and a flaky handler.
"""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

log = logging.getLogger("devcopilot.jobqueue")


@dataclass
class Job:
    kind: str
    payload: dict
    attempts: int = 0
    id: str = ""


@dataclass
class InMemoryJobQueue:
    """Per-process asyncio queue + an in-memory dead-letter list."""

    _q: "asyncio.Queue[Job]" = field(default_factory=asyncio.Queue)
    dead_letter: list[Job] = field(default_factory=list)

    async def enqueue(self, job: Job) -> None:
        await self._q.put(job)

    async def dequeue(self) -> Job:
        return await self._q.get()

    async def to_dead_letter(self, job: Job) -> None:
        self.dead_letter.append(job)

    def qsize(self) -> int:
        return self._q.qsize()


class RedisJobQueue:
    """Redis-list-backed queue: LPUSH to enqueue, BRPOP to dequeue, a separate list
    for dead-letter. Jobs survive a restart and any replica can consume them."""

    def __init__(self, client: Any, key: str = "copilot:jobs"):
        self._redis = client
        self._key = key
        self._dlq = f"{key}:dead"

    async def enqueue(self, job: Job) -> None:
        await self._redis.lpush(self._key, json.dumps(job.__dict__))

    async def dequeue(self) -> Job:
        _, raw = await self._redis.brpop(self._key)
        return Job(**json.loads(raw))

    async def to_dead_letter(self, job: Job) -> None:
        await self._redis.lpush(self._dlq, json.dumps(job.__dict__))


async def process_one(
    queue: Any,
    handler: Callable[[Job], Awaitable[None]],
    max_attempts: int = 3,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
) -> str:
    """Dequeue one job and run it. On failure, re-enqueue with backoff until
    max_attempts, then dead-letter. Returns the outcome: done|retried|dead. Pure
    over the injected queue/handler/sleep, so it's unit-testable."""
    job = await queue.dequeue()
    try:
        await handler(job)
        return "done"
    except Exception:  # noqa: BLE001 — a failed job is retried/dead-lettered, not fatal
        job.attempts += 1
        if job.attempts >= max_attempts:
            log.warning("job %s/%s dead-lettered after %d attempts", job.kind, job.id, job.attempts)
            await queue.to_dead_letter(job)
            return "dead"
        log.warning("job %s/%s failed (attempt %d) — retrying", job.kind, job.id, job.attempts)
        await sleep(min(30.0, 0.5 * (2 ** (job.attempts - 1))))
        await queue.enqueue(job)
        return "retried"


async def run_worker(queue: Any, handler: Callable[[Job], Awaitable[None]], max_attempts: int = 3) -> None:
    """Continuously process jobs until cancelled."""
    log.info("job worker started")
    while True:
        try:
            await process_one(queue, handler, max_attempts)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 — a queue hiccup shouldn't kill the worker
            log.exception("job worker loop error")
            await asyncio.sleep(1.0)


def make_job_queue(redis_url: str) -> Any:
    """Redis-backed queue when a URL is set (durable, multi-replica), else in-memory."""
    if redis_url.strip():
        try:
            import redis.asyncio as aioredis

            log.info("job queue: redis")
            return RedisJobQueue(aioredis.from_url(redis_url, decode_responses=True))
        except Exception:  # noqa: BLE001 — degrade to in-memory
            log.warning("redis job queue unavailable; using in-memory", exc_info=True)
    return InMemoryJobQueue()
