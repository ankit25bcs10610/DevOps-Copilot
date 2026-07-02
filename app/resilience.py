"""Resilience primitives — bounded retry with backoff + a circuit breaker.

An agent that drives a remote LLM and remote tool servers must survive transient
failures (429s, 5xx, timeouts) without hammering a struggling dependency. This
module provides two small, dependency-free, unit-testable primitives:

  - retry_call: bounded exponential backoff over a retryable-error predicate.
  - CircuitBreaker: trip open after N consecutive failures, fail fast while open,
    then probe half-open after a cooldown — so we stop pounding a dead provider.

Both take injectable clock/sleep so the logic is deterministic in tests, and both
are provider-agnostic (used to wrap LLM calls in app/llm.py, and reusable for MCP).
"""

from __future__ import annotations

import logging
import time
from typing import Callable, Iterable, TypeVar

log = logging.getLogger("devcopilot.resilience")

T = TypeVar("T")

# Substrings that mark a transient, worth-retrying failure. Matched case-insensitively
# against str(exc) so we stay provider-agnostic (no hard SDK dependency).
_RETRYABLE_MARKERS = (
    "429", "rate limit", "overloaded", "timeout", "timed out", "503", "502", "500",
    "connection", "temporarily", "unavailable", "econnreset", "read timed out",
)


def is_retryable(exc: BaseException, markers: Iterable[str] = _RETRYABLE_MARKERS) -> bool:
    """Heuristic: does this exception look transient (safe to retry)?"""
    text = f"{type(exc).__name__}: {exc}".lower()
    return any(m in text for m in markers)


def backoff_delays(attempts: int, base: float, cap: float) -> list[float]:
    """Deterministic exponential backoff schedule (no jitter, so it's testable):
    base, 2*base, 4*base, … capped at `cap`. Length attempts-1 (delays BETWEEN tries)."""
    return [min(cap, base * (2 ** i)) for i in range(max(0, attempts - 1))]


def retry_call(
    fn: Callable[[], T],
    *,
    attempts: int = 3,
    base_delay: float = 0.5,
    max_delay: float = 8.0,
    retryable: Callable[[BaseException], bool] = is_retryable,
    sleep: Callable[[float], None] = time.sleep,
    on_retry: Callable[[int, BaseException], None] | None = None,
) -> T:
    """Call `fn`, retrying transient failures with exponential backoff. Re-raises the
    last exception if all attempts fail or the error isn't retryable."""
    delays = backoff_delays(attempts, base_delay, max_delay)
    last: BaseException | None = None
    for i in range(max(1, attempts)):
        try:
            return fn()
        except BaseException as exc:  # noqa: BLE001 — we re-raise non-retryable/last below
            last = exc
            if i >= attempts - 1 or not retryable(exc):
                raise
            if on_retry:
                on_retry(i + 1, exc)
            log.warning("retryable failure (attempt %d/%d): %s", i + 1, attempts, exc)
            sleep(delays[i])
    assert last is not None  # unreachable: loop either returns or raises
    raise last


class CircuitOpenError(RuntimeError):
    """Raised when a call is attempted while the breaker is open (failing fast)."""


class CircuitBreaker:
    """Trip open after `failure_threshold` consecutive failures; fail fast for
    `reset_timeout` seconds; then allow one half-open probe that closes on success
    or re-opens on failure. Clock is injectable for deterministic tests."""

    def __init__(self, failure_threshold: int = 5, reset_timeout: float = 30.0,
                 clock: Callable[[], float] = time.monotonic):
        self.failure_threshold = max(1, failure_threshold)
        self.reset_timeout = reset_timeout
        self._clock = clock
        self._failures = 0
        self._opened_at: float | None = None

    @property
    def state(self) -> str:
        if self._opened_at is None:
            return "closed"
        if self._clock() - self._opened_at >= self.reset_timeout:
            return "half_open"
        return "open"

    def call(self, fn: Callable[[], T]) -> T:
        state = self.state
        if state == "open":
            raise CircuitOpenError("circuit open — failing fast")
        try:
            result = fn()
        except BaseException:  # noqa: BLE001 — record the failure, then re-raise
            self._on_failure()
            raise
        self._on_success()
        return result

    def _on_success(self) -> None:
        self._failures = 0
        self._opened_at = None

    def _on_failure(self) -> None:
        self._failures += 1
        if self._failures >= self.failure_threshold:
            self._opened_at = self._clock()
