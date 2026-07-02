"""Resilience primitives — retry/backoff + circuit breaker (pure, deterministic)."""

import pytest

from app.resilience import (
    CircuitBreaker,
    CircuitOpenError,
    backoff_delays,
    is_retryable,
    retry_call,
)


def test_is_retryable_classifies_transient_vs_permanent():
    assert is_retryable(RuntimeError("429 Too Many Requests"))
    assert is_retryable(TimeoutError("read timed out"))
    assert is_retryable(Exception("service temporarily unavailable (503)"))
    assert not is_retryable(ValueError("invalid api key"))
    assert not is_retryable(KeyError("missing field"))


def test_backoff_delays_exponential_and_capped():
    assert backoff_delays(4, base=0.5, cap=8.0) == [0.5, 1.0, 2.0]
    assert backoff_delays(5, base=1.0, cap=2.0) == [1.0, 2.0, 2.0, 2.0]
    assert backoff_delays(1, base=0.5, cap=8.0) == []  # no delays for a single attempt


def test_retry_call_succeeds_after_transient_failures():
    calls = {"n": 0}
    slept: list[float] = []

    def flaky():
        calls["n"] += 1
        if calls["n"] < 3:
            raise RuntimeError("503 unavailable")
        return "ok"

    out = retry_call(flaky, attempts=3, base_delay=0.1, sleep=slept.append)
    assert out == "ok"
    assert calls["n"] == 3
    assert slept == [0.1, 0.2]  # backoff between the two retries


def test_retry_call_reraises_after_exhaustion():
    with pytest.raises(RuntimeError, match="429"):
        retry_call(lambda: (_ for _ in ()).throw(RuntimeError("429")),
                   attempts=2, base_delay=0, sleep=lambda _s: None)


def test_retry_call_does_not_retry_permanent_error():
    calls = {"n": 0}

    def perm():
        calls["n"] += 1
        raise ValueError("invalid api key")

    with pytest.raises(ValueError):
        retry_call(perm, attempts=5, sleep=lambda _s: None)
    assert calls["n"] == 1  # not retried


def test_circuit_breaker_opens_then_half_opens_then_closes():
    now = [1000.0]
    cb = CircuitBreaker(failure_threshold=2, reset_timeout=30.0, clock=lambda: now[0])

    def boom():
        raise RuntimeError("503")

    assert cb.state == "closed"
    for _ in range(2):
        with pytest.raises(RuntimeError):
            cb.call(boom)
    assert cb.state == "open"

    # While open, calls fail fast without invoking fn.
    with pytest.raises(CircuitOpenError):
        cb.call(lambda: "should not run")

    # After the cooldown it half-opens and a success closes it.
    now[0] += 31
    assert cb.state == "half_open"
    assert cb.call(lambda: "recovered") == "recovered"
    assert cb.state == "closed"


def test_circuit_breaker_success_resets_failure_count():
    cb = CircuitBreaker(failure_threshold=3, clock=lambda: 0.0)
    with pytest.raises(RuntimeError):
        cb.call(lambda: (_ for _ in ()).throw(RuntimeError("timeout")))
    cb.call(lambda: "ok")  # success resets
    # One more failure should not trip (count was reset).
    with pytest.raises(RuntimeError):
        cb.call(lambda: (_ for _ in ()).throw(RuntimeError("timeout")))
    assert cb.state == "closed"
