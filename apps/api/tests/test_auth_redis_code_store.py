"""Integration smoke for `RedisCodeStore`.

Skipped when Redis isn't reachable so the file degrades gracefully
without docker. Each test cleans up the keys it sets to avoid
cross-test leakage on a re-run.

Key namespace `auth:sms_code:<phone>` mirrors what `RedisCodeStore`
writes internally — tests delete via the same pattern so they don't
race with the application's lazy TTL behaviour.
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import AsyncIterator
from datetime import timedelta

import pytest
import pytest_asyncio
from app.config import get_settings
from app.services.auth.code_store import RedisCodeStore
from redis.asyncio import Redis


@pytest_asyncio.fixture
async def redis_client() -> AsyncIterator[Redis]:
    """Per-test redis client. Skip when Redis is unreachable so this
    file is safe to run locally without docker."""
    client: Redis = Redis.from_url(get_settings().redis_url)
    try:
        await client.ping()
    except Exception as exc:
        await client.aclose()
        pytest.skip(f"redis unreachable at {get_settings().redis_url}: {exc}")
    try:
        yield client
    finally:
        await client.aclose()


@pytest_asyncio.fixture
async def store(redis_client: Redis) -> AsyncIterator[RedisCodeStore]:
    yield RedisCodeStore(redis_client)


def _unique_phone() -> str:
    """Mainland-format phone unlikely to collide with other tests."""
    suffix = uuid.uuid4().int % 10**8
    return f"139{suffix:08d}"


async def _cleanup(redis_client: Redis, phones: list[str]) -> None:
    if not phones:
        return
    keys = [f"auth:sms_code:{p}" for p in phones]
    await redis_client.delete(*keys)


@pytest.mark.integration
async def test_set_then_get_round_trips(
    store: RedisCodeStore,
    redis_client: Redis,
) -> None:
    phone = _unique_phone()
    try:
        await store.set(phone, "123456", ttl=timedelta(minutes=5))

        loaded = await store.get(phone)
        assert loaded == "123456"
    finally:
        await _cleanup(redis_client, [phone])


@pytest.mark.integration
async def test_get_unknown_phone_returns_none(store: RedisCodeStore) -> None:
    assert await store.get(_unique_phone()) is None


@pytest.mark.integration
async def test_set_overwrites_previous_code(
    store: RedisCodeStore,
    redis_client: Redis,
) -> None:
    """A re-request of /auth/sms/send must invalidate the prior code —
    otherwise a user could verify against a stale code after re-requesting."""
    phone = _unique_phone()
    try:
        await store.set(phone, "111111", ttl=timedelta(minutes=5))
        await store.set(phone, "222222", ttl=timedelta(minutes=5))

        assert await store.get(phone) == "222222"
    finally:
        await _cleanup(redis_client, [phone])


@pytest.mark.integration
async def test_pop_returns_code_and_atomically_retires_it(
    store: RedisCodeStore,
    redis_client: Redis,
) -> None:
    """GETDEL semantics — a successful verify must retire the code so
    a parallel verify retry on the same phone can't replay it."""
    phone = _unique_phone()
    try:
        await store.set(phone, "654321", ttl=timedelta(minutes=5))

        first = await store.pop(phone)
        assert first == "654321"

        # Second pop must see nothing; the GET-then-DEL had to be
        # atomic for this to hold under contention.
        second = await store.pop(phone)
        assert second is None

        # And the regular get sees nothing too.
        assert await store.get(phone) is None
    finally:
        await _cleanup(redis_client, [phone])


@pytest.mark.integration
async def test_pop_unknown_phone_returns_none(store: RedisCodeStore) -> None:
    assert await store.pop(_unique_phone()) is None


@pytest.mark.integration
async def test_short_ttl_expires_the_code(
    store: RedisCodeStore,
    redis_client: Redis,
) -> None:
    """Redis EX rounds up to whole seconds — a 1-second TTL plus a
    short sleep covers the expiry path without making the test flaky."""
    phone = _unique_phone()
    try:
        await store.set(phone, "111111", ttl=timedelta(seconds=1))
        # Sleep slightly past 1s so Redis has definitely evicted.
        await asyncio.sleep(1.5)

        assert await store.get(phone) is None
        assert await store.pop(phone) is None
    finally:
        await _cleanup(redis_client, [phone])


@pytest.mark.integration
async def test_concurrent_pop_only_one_wins(
    store: RedisCodeStore,
    redis_client: Redis,
) -> None:
    """The hot replay-attack story: two `/verify` calls land at the
    same instant. GETDEL guarantees exactly one sees the code."""
    phone = _unique_phone()
    try:
        await store.set(phone, "999999", ttl=timedelta(minutes=5))

        # Launch a handful of concurrent pops; only one should win.
        results = await asyncio.gather(*(store.pop(phone) for _ in range(8)))
        winners = [r for r in results if r is not None]
        assert winners == ["999999"]
    finally:
        await _cleanup(redis_client, [phone])
