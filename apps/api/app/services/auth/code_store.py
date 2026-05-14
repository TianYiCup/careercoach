"""SMS verification code store.

The store maps `phone → (code, expires_at)`. SMS verify reads-and-deletes
on first match (one-shot codes); SMS send overwrites any existing code
for the same phone so a re-request invalidates the prior one.

Two implementations:
  * `InMemoryCodeStore` — dict-backed; default for dev / tests where
    docker isn't available.
  * `RedisCodeStore` — production. Uses SET … EX for atomic TTL writes
    and GETDEL (Redis 6.2+) for one-shot pop so two concurrent verify
    calls can't both see the same code.

Why Redis (not Postgres) for codes: 5-minute TTL, expected write rate
is moderate per phone, and the store needs fast atomic GET+DEL.
Postgres could do it via `DELETE … RETURNING` but the wider story is
that codes are session ephemera, not durable identity data.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Protocol, runtime_checkable

import structlog
from redis.asyncio import Redis

logger = structlog.get_logger(__name__)

_KEY_PREFIX = "auth:sms_code:"


@dataclass(frozen=True)
class StoredCode:
    """The minimum the store needs to remember. `expires_at` is UTC."""

    code: str
    expires_at: datetime


@runtime_checkable
class CodeStore(Protocol):
    """Phone-keyed one-shot SMS code store. Implementations must
    enforce expiry on `get` so callers don't have to re-check."""

    async def set(self, phone: str, code: str, *, ttl: timedelta) -> None: ...

    async def get(self, phone: str) -> str | None:
        """Return the un-expired code for `phone`, or None.

        Does NOT consume — verify uses `pop` to atomically check + retire
        the code. `get` is exposed for tests + future audit needs.
        """
        ...

    async def pop(self, phone: str) -> str | None:
        """Read-and-delete. Returns None for missing/expired entries."""
        ...


class InMemoryCodeStore:
    """Dict-backed store with explicit expiry checks.

    Not thread-safe — single uvicorn worker assumption holds for v0.
    The Redis-backed implementation uses SET … EX so expiry is the
    server's job, not the application's.
    """

    def __init__(self) -> None:
        self._store: dict[str, StoredCode] = {}

    async def set(self, phone: str, code: str, *, ttl: timedelta) -> None:
        self._store[phone] = StoredCode(
            code=code,
            expires_at=datetime.now(UTC) + ttl,
        )

    async def get(self, phone: str) -> str | None:
        entry = self._store.get(phone)
        if entry is None:
            return None
        if datetime.now(UTC) >= entry.expires_at:
            # Lazy eviction — avoids a background sweeper for v0.
            self._store.pop(phone, None)
            return None
        return entry.code

    async def pop(self, phone: str) -> str | None:
        entry = self._store.pop(phone, None)
        if entry is None:
            return None
        if datetime.now(UTC) >= entry.expires_at:
            return None
        return entry.code


class RedisCodeStore:
    """Redis-backed implementation.

    Uses `SET key value EX seconds` for atomic write+TTL — no risk of a
    code lingering past its TTL window because the application forgot
    to evict. `pop` uses `GETDEL` (Redis 6.2+, satisfied by our
    `redis:7-alpine` image) for an atomic read+delete so two
    concurrent /verify calls can't both see the same code.

    Phone numbers are namespaced under `auth:sms_code:<phone>` so a
    future code store on the same Redis (e.g. rate-limit counters)
    doesn't collide.
    """

    def __init__(self, client: Redis) -> None:
        self._client = client

    async def set(self, phone: str, code: str, *, ttl: timedelta) -> None:
        await self._client.set(
            _key(phone),
            code,
            ex=int(ttl.total_seconds()),
        )

    async def get(self, phone: str) -> str | None:
        value = await self._client.get(_key(phone))
        return _decode(value)

    async def pop(self, phone: str) -> str | None:
        # GETDEL retires the code in one round trip so a parallel
        # `/verify` retry can't replay it.
        value = await self._client.getdel(_key(phone))
        return _decode(value)


def _key(phone: str) -> str:
    return f"{_KEY_PREFIX}{phone}"


def _decode(value: bytes | str | None) -> str | None:
    """`redis-py` returns bytes by default; some clients are configured
    with `decode_responses=True` and return str. Cover both."""
    if value is None:
        return None
    if isinstance(value, bytes):
        return value.decode("utf-8")
    return value


__all__ = ["CodeStore", "InMemoryCodeStore", "RedisCodeStore", "StoredCode"]
