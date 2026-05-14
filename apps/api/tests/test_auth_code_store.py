"""Tests for `InMemoryCodeStore`.

Covers set/get/pop semantics and TTL expiry. The expiry tests use a
1-millisecond TTL + a small sleep so we don't pin wall-clock behaviour.
"""

from __future__ import annotations

import asyncio
from datetime import timedelta

from app.services.auth.code_store import InMemoryCodeStore


async def test_set_then_get_returns_stored_code() -> None:
    store = InMemoryCodeStore()
    await store.set("13800138000", "123456", ttl=timedelta(minutes=5))

    assert await store.get("13800138000") == "123456"


async def test_get_returns_none_for_unknown_phone() -> None:
    store = InMemoryCodeStore()
    assert await store.get("13800138000") is None


async def test_set_overwrites_previous_code_for_same_phone() -> None:
    """SMS-send must invalidate the prior code so a re-request retires
    the old one — otherwise a user could verify against a stale code
    after re-requesting."""
    store = InMemoryCodeStore()
    await store.set("13800138000", "111111", ttl=timedelta(minutes=5))
    await store.set("13800138000", "222222", ttl=timedelta(minutes=5))

    assert await store.get("13800138000") == "222222"


async def test_pop_returns_code_and_removes_it() -> None:
    """One-shot semantics — pop must retire the code so the same code
    can't be replayed."""
    store = InMemoryCodeStore()
    await store.set("13800138000", "654321", ttl=timedelta(minutes=5))

    first = await store.pop("13800138000")
    assert first == "654321"

    second = await store.pop("13800138000")
    assert second is None


async def test_pop_returns_none_for_unknown_phone() -> None:
    store = InMemoryCodeStore()
    assert await store.pop("13800138000") is None


async def test_get_treats_expired_code_as_missing() -> None:
    """A ttl that's already elapsed by the time we check must return
    None rather than the cached value."""
    store = InMemoryCodeStore()
    await store.set("13800138000", "111111", ttl=timedelta(milliseconds=1))

    # Sleep just past the expiry window. 50ms is generous on any CI runner.
    await asyncio.sleep(0.05)

    assert await store.get("13800138000") is None


async def test_pop_treats_expired_code_as_missing() -> None:
    store = InMemoryCodeStore()
    await store.set("13800138000", "111111", ttl=timedelta(milliseconds=1))
    await asyncio.sleep(0.05)

    assert await store.pop("13800138000") is None
