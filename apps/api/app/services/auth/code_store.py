"""SMS verification code store — Protocol + in-memory impl.

The store maps `phone → (code, expires_at)`. SMS verify reads-and-deletes
on first match (one-shot codes); SMS send overwrites any existing code
for the same phone so a re-request invalidates the prior one.

v0 ships the in-memory impl. The real Redis-backed implementation
will drop into the same Protocol when we wire Redis.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Protocol, runtime_checkable


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
    The Redis-backed implementation will use SETEX so expiry is the
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


__all__ = ["CodeStore", "InMemoryCodeStore", "StoredCode"]
