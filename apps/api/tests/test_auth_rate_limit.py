"""Unit tests for `InMemoryRateLimiter`.

Pins the policy contract on the in-memory implementation:

  * Send cooldown blocks a second send within the window and reports
    the seconds-left correctly.
  * Verify lockout fires on the Nth failure and exposes the remaining
    seconds; Nth+1 attempt sees the lock.
  * `reset_verify_state` immediately clears both the failure counter
    and the lockout (the success path needs this — a legitimate user
    who finally guesses right shouldn't stay locked).

Redis-backed counterpart lives in `test_auth_redis_rate_limit.py` and
re-tests the same policies against a real Redis. Both implementations
must satisfy the same `RateLimiter` Protocol, but the policy is what
the route layer depends on, so it's pinned twice deliberately.
"""

from __future__ import annotations

import asyncio
from datetime import timedelta

import pytest
from app.services.auth.rate_limit import (
    MAX_VERIFY_FAILURES,
    SEND_COOLDOWN,
    VERIFY_LOCK_DURATION,
    InMemoryRateLimiter,
    RateLimited,
)


def test_rate_limited_exception_carries_kind_and_retry_seconds() -> None:
    exc = RateLimited(kind="send_cooldown", retry_after_seconds=42)
    assert exc.kind == "send_cooldown"
    assert exc.retry_after_seconds == 42
    # __str__ should be informative for unhandled-exception logs.
    assert "send_cooldown" in str(exc)
    assert "42" in str(exc)


def test_constants_match_prd_F1_L2() -> None:
    """The PRD-anchored numbers — pinned so a future tightening shows
    up in tests instead of silently skewing what we promise the user."""
    assert timedelta(seconds=60) == SEND_COOLDOWN
    assert MAX_VERIFY_FAILURES == 3
    assert timedelta(minutes=5) == VERIFY_LOCK_DURATION


# --- Send cooldown -----------------------------------------------------------


async def test_send_cooldown_returns_none_when_no_prior_send() -> None:
    limiter = InMemoryRateLimiter()
    assert await limiter.get_send_cooldown_seconds("13800138000") is None


async def test_send_cooldown_blocks_immediate_resend() -> None:
    limiter = InMemoryRateLimiter()
    await limiter.mark_send("13800138000", cooldown=SEND_COOLDOWN)

    seconds_left = await limiter.get_send_cooldown_seconds("13800138000")
    assert seconds_left is not None
    # Allow some clock skew but it must report at least 1 (never 0,
    # which would tell the client "retry now").
    assert 1 <= seconds_left <= 60


async def test_send_cooldown_is_per_phone_not_global() -> None:
    """Re-sending for phone A must not block phone B — keeping the cap
    per phone is what makes it usable for multi-user dev."""
    limiter = InMemoryRateLimiter()
    await limiter.mark_send("13800138000", cooldown=SEND_COOLDOWN)

    assert await limiter.get_send_cooldown_seconds("13800138001") is None


async def test_send_cooldown_expires() -> None:
    limiter = InMemoryRateLimiter()
    await limiter.mark_send("13800138000", cooldown=timedelta(milliseconds=50))

    await asyncio.sleep(0.1)

    assert await limiter.get_send_cooldown_seconds("13800138000") is None


# --- Verify lockout ----------------------------------------------------------


async def test_verify_lock_returns_none_before_any_failure() -> None:
    limiter = InMemoryRateLimiter()
    assert await limiter.get_verify_lock_seconds("13800138000") is None


async def test_verify_lock_does_not_fire_below_threshold() -> None:
    limiter = InMemoryRateLimiter()

    for _ in range(MAX_VERIFY_FAILURES - 1):
        await limiter.record_verify_failure(
            "13800138000",
            max_failures=MAX_VERIFY_FAILURES,
            lock_duration=VERIFY_LOCK_DURATION,
        )

    assert await limiter.get_verify_lock_seconds("13800138000") is None


async def test_verify_lock_fires_on_threshold_failure() -> None:
    """The PRD pin: 3 wrong attempts → lock. The Nth call must return
    a count of N (proves the increment is atomic) and the lock must be
    visible immediately after."""
    limiter = InMemoryRateLimiter()

    counts = []
    for _ in range(MAX_VERIFY_FAILURES):
        counts.append(
            await limiter.record_verify_failure(
                "13800138000",
                max_failures=MAX_VERIFY_FAILURES,
                lock_duration=VERIFY_LOCK_DURATION,
            )
        )

    assert counts == list(range(1, MAX_VERIFY_FAILURES + 1))

    seconds_left = await limiter.get_verify_lock_seconds("13800138000")
    assert seconds_left is not None
    assert 1 <= seconds_left <= int(VERIFY_LOCK_DURATION.total_seconds())


async def test_verify_lock_is_per_phone() -> None:
    limiter = InMemoryRateLimiter()
    for _ in range(MAX_VERIFY_FAILURES):
        await limiter.record_verify_failure(
            "13800138000",
            max_failures=MAX_VERIFY_FAILURES,
            lock_duration=VERIFY_LOCK_DURATION,
        )

    # Phone B is independent — must not be locked.
    assert await limiter.get_verify_lock_seconds("13800138001") is None


async def test_failure_window_is_anchored_at_first_failure() -> None:
    """A new failure must NOT push the lock further into the future —
    otherwise an attacker could stay below the cap forever by spacing
    their guesses just under the window."""
    limiter = InMemoryRateLimiter()

    await limiter.record_verify_failure(
        "13800138000",
        max_failures=MAX_VERIFY_FAILURES,
        lock_duration=timedelta(milliseconds=200),
    )
    # Wait nearly the full window, then fire a second failure.
    await asyncio.sleep(0.15)
    await limiter.record_verify_failure(
        "13800138000",
        max_failures=MAX_VERIFY_FAILURES,
        lock_duration=timedelta(milliseconds=200),
    )
    # After waiting past the original window, the counter resets
    # because the anchor was at failure 1.
    await asyncio.sleep(0.1)

    count = await limiter.record_verify_failure(
        "13800138000",
        max_failures=MAX_VERIFY_FAILURES,
        lock_duration=timedelta(milliseconds=200),
    )
    # The original window expired, so this is a fresh count starting at 1.
    assert count == 1


async def test_reset_verify_state_clears_counter_and_lock() -> None:
    """Success path: the limiter must drop both the counter and the
    lockout in one call, otherwise a user who finally gets the right
    code stays locked for the rest of the window."""
    limiter = InMemoryRateLimiter()
    for _ in range(MAX_VERIFY_FAILURES):
        await limiter.record_verify_failure(
            "13800138000",
            max_failures=MAX_VERIFY_FAILURES,
            lock_duration=VERIFY_LOCK_DURATION,
        )
    assert await limiter.get_verify_lock_seconds("13800138000") is not None

    await limiter.reset_verify_state("13800138000")

    assert await limiter.get_verify_lock_seconds("13800138000") is None
    # Counter is also dropped — next failure is a fresh count of 1.
    fresh_count = await limiter.record_verify_failure(
        "13800138000",
        max_failures=MAX_VERIFY_FAILURES,
        lock_duration=VERIFY_LOCK_DURATION,
    )
    assert fresh_count == 1


async def test_reset_verify_state_is_safe_when_no_state() -> None:
    """Idempotent — the success path always calls reset, even on a
    first-attempt success where there was nothing to reset."""
    limiter = InMemoryRateLimiter()
    # Should not raise.
    await limiter.reset_verify_state("13800138000")


@pytest.mark.parametrize("phone", ["13800138000", "13900139000", "15800158000"])
async def test_record_verify_failure_returns_strictly_increasing_counts(
    phone: str,
) -> None:
    limiter = InMemoryRateLimiter()
    counts = []
    for _ in range(5):
        counts.append(
            await limiter.record_verify_failure(
                phone,
                max_failures=MAX_VERIFY_FAILURES,
                lock_duration=VERIFY_LOCK_DURATION,
            )
        )
    assert counts == [1, 2, 3, 4, 5]
