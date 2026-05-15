"""Service-level tests for the rate-limit policy through `AuthService`.

`test_auth_rate_limit.py` pins the limiter primitive; this file pins
how `send_code` / `verify_code` USE the limiter — i.e. the integration
between the orchestrator and the policy.
"""

from __future__ import annotations

from datetime import timedelta

import pytest
from app.services.auth.code_store import InMemoryCodeStore
from app.services.auth.rate_limit import (
    MAX_VERIFY_FAILURES,
    InMemoryRateLimiter,
    RateLimited,
)
from app.services.auth.service import (
    AuthService,
    InvalidCodeError,
    LoggingDispatcher,
)
from app.services.auth.user_repository import InMemoryUserRepository


def _service() -> tuple[AuthService, InMemoryCodeStore, InMemoryRateLimiter]:
    store = InMemoryCodeStore()
    limiter = InMemoryRateLimiter()
    svc = AuthService(
        code_store=store,
        user_repo=InMemoryUserRepository(),
        dispatcher=LoggingDispatcher(),
        rate_limiter=limiter,
    )
    return svc, store, limiter


# --- Send cooldown -----------------------------------------------------------


async def test_send_code_marks_phone_for_cooldown() -> None:
    svc, _, limiter = _service()
    await svc.send_code("13800138000")
    assert await limiter.get_send_cooldown_seconds("13800138000") is not None


async def test_second_send_within_cooldown_raises_rate_limited() -> None:
    svc, _, _ = _service()
    await svc.send_code("13800138000")

    with pytest.raises(RateLimited) as exc_info:
        await svc.send_code("13800138000")

    assert exc_info.value.kind == "send_cooldown"
    assert exc_info.value.retry_after_seconds >= 1


async def test_blocked_send_does_not_dispatch_or_store_a_new_code() -> None:
    """Cost discipline: a rate-limited send must NOT touch the SMS
    gateway (would cost money in prod) or overwrite the in-flight
    code (would invalidate the legitimate user's pending verify)."""
    svc, store, _ = _service()
    await svc.send_code("13800138000")
    original_code = await store.get("13800138000")
    assert original_code is not None

    with pytest.raises(RateLimited):
        await svc.send_code("13800138000")

    # The in-flight code is untouched.
    assert await store.get("13800138000") == original_code


async def test_send_for_different_phone_is_unaffected() -> None:
    svc, _, _ = _service()
    await svc.send_code("13800138000")
    # No exception — different phone, fresh window.
    await svc.send_code("13800138001")


# --- Verify lockout ----------------------------------------------------------


async def test_three_wrong_codes_lock_verify() -> None:
    svc, store, _ = _service()
    await svc.send_code("13800138000")
    real_code = await store.get("13800138000")
    assert real_code is not None

    # Each wrong guess pops the code, so we re-seed between attempts.
    # The intent is to prove the LIMITER counts failures across these
    # re-seeded attempts and locks on the Nth.
    for _ in range(MAX_VERIFY_FAILURES):
        await store.set("13800138000", "111111", ttl=timedelta(minutes=5))
        with pytest.raises(InvalidCodeError):
            await svc.verify_code("13800138000", "000000")

    # The next verify should be locked, even with the right code.
    await store.set("13800138000", real_code, ttl=timedelta(minutes=5))
    with pytest.raises(RateLimited) as exc_info:
        await svc.verify_code("13800138000", real_code)
    assert exc_info.value.kind == "verify_locked"
    assert exc_info.value.retry_after_seconds >= 1


async def test_verify_with_no_pending_code_still_counts_as_failure() -> None:
    """Attacker who skips /send and brute-forces /verify must still
    be subject to the lockout — otherwise the cap is trivially
    bypassable by never calling /send."""
    svc, _, limiter = _service()

    for _ in range(MAX_VERIFY_FAILURES):
        with pytest.raises(InvalidCodeError):
            await svc.verify_code("13800138000", "000000")

    assert await limiter.get_verify_lock_seconds("13800138000") is not None


async def test_successful_verify_resets_lockout_state() -> None:
    """A user who finally guesses right after 2 wrong attempts must
    NOT carry forward the failure counter into the next session."""
    svc, store, limiter = _service()

    # Two failures (under the 3-strike threshold).
    for _ in range(2):
        await store.set("13800138000", "111111", ttl=timedelta(minutes=5))
        with pytest.raises(InvalidCodeError):
            await svc.verify_code("13800138000", "000000")

    # Third attempt is the right code.
    await store.set("13800138000", "987654", ttl=timedelta(minutes=5))
    await svc.verify_code("13800138000", "987654")

    # Counter cleared — next failure starts a fresh window.
    assert await limiter.get_verify_lock_seconds("13800138000") is None
    fresh_count = await limiter.record_verify_failure(
        "13800138000",
        max_failures=MAX_VERIFY_FAILURES,
        lock_duration=timedelta(minutes=5),
    )
    assert fresh_count == 1


async def test_verify_during_lockout_does_not_pop_the_code() -> None:
    """The lock fires BEFORE the code-store pop, so an attacker who
    finally figures out the right code can't burn it during the lock
    window — the legitimate user can use it once unlocked."""
    svc, store, _ = _service()

    # Drive into lock.
    for _ in range(MAX_VERIFY_FAILURES):
        with pytest.raises(InvalidCodeError):
            await svc.verify_code("13800138000", "000000")

    # Seed a real code; it should still be present after a locked attempt.
    await store.set("13800138000", "654321", ttl=timedelta(minutes=5))
    with pytest.raises(RateLimited):
        await svc.verify_code("13800138000", "654321")

    assert await store.get("13800138000") == "654321"
