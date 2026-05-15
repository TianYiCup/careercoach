"""Per-phone rate limit + verify-failure lockout — PRD §F1 L2.

Two policies:

  * **Send cooldown** — after `/v1/auth/sms/send` succeeds for a phone,
    block subsequent send requests for the same phone for 60 s. Prevents
    SMS bombing as a cost-of-service attack and matches the client-side
    resend cooldown the UI already shows.

  * **Verify lockout** — count wrong codes per phone in a sliding 5-min
    window; on the 3rd failure, lock `/v1/auth/sms/verify` for the same
    phone for 5 min. Prevents brute-forcing 6-digit codes.

Both policies key on **phone**, not IP, because PRD §F1 L2 phrases the
limit per phone and because we don't trust caller IP behind the
production reverse proxy without a separate `X-Forwarded-For` policy.

Two implementations:

  * `InMemoryRateLimiter` — dict-backed, single-worker. Default for dev
    and tests so the suite runs without docker.
  * `RedisRateLimiter` — production. Uses `SET … EX` for cooldown and
    a transactional `INCR` + `EXPIRE NX` for the failure counter so two
    concurrent verifies can't both bypass the cap by interleaving.

Why a custom limiter (not `slowapi` / `fastapi-limiter`): the policy is
two-sided (cooldown on `/send`, lockout on `/verify`) and must reset
on a successful verify so a legitimate user who finally gets the right
code doesn't stay locked for the rest of the window. Off-the-shelf
middleware is one-sided and doesn't expose a "reset on success" hook.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Literal, Protocol, runtime_checkable

import structlog
from redis.asyncio import Redis

logger = structlog.get_logger(__name__)

# Redis keyspaces — namespaced under `auth:rl:` so a future limiter on
# the same Redis (e.g. per-IP) doesn't collide with us or with the
# `auth:sms_code:*` keyspace owned by `code_store.py`.
_SEND_KEY_PREFIX = "auth:rl:send:"
_FAILS_KEY_PREFIX = "auth:rl:verify_fails:"
_LOCK_KEY_PREFIX = "auth:rl:verify_lock:"

# v0 policy values surfaced as constants here so the service layer can
# import them without re-deriving the PRD numbers. Tests also import
# these so a future tightening of the cap automatically propagates.
SEND_COOLDOWN = timedelta(seconds=60)
MAX_VERIFY_FAILURES = 3
VERIFY_LOCK_DURATION = timedelta(minutes=5)

RateLimitKind = Literal["send_cooldown", "verify_locked"]


class RateLimited(RuntimeError):
    """Raised when a rate limit / lockout fires.

    The route layer maps this to 429 + `Retry-After: <seconds>` per
    RFC 6585. `kind` distinguishes the two policies so the route can
    pick the right user-facing copy.
    """

    def __init__(self, *, kind: RateLimitKind, retry_after_seconds: int) -> None:
        self.kind: RateLimitKind = kind
        self.retry_after_seconds = retry_after_seconds
        super().__init__(f"rate_limited({kind}): retry after {retry_after_seconds}s")


@runtime_checkable
class RateLimiter(Protocol):
    """Phone-keyed rate limit + lockout state.

    All methods are async because the Redis impl is async-native.
    """

    async def get_send_cooldown_seconds(self, phone: str) -> int | None:
        """Seconds left on the send-cooldown for `phone`, or None if a
        send is currently allowed. Implementations MUST round up to at
        least 1 when there's any time left so the client never sees a
        retry-after of 0."""

    async def mark_send(self, phone: str, *, cooldown: timedelta) -> None:
        """Block subsequent sends for `phone` for `cooldown`."""

    async def get_verify_lock_seconds(self, phone: str) -> int | None:
        """Seconds left on the verify-lockout for `phone`, or None."""

    async def record_verify_failure(
        self,
        phone: str,
        *,
        max_failures: int,
        lock_duration: timedelta,
    ) -> int:
        """Atomically increment the failure counter for `phone` and
        return the new count. The counter has a TTL of `lock_duration`
        so the window slides shut after that much inactivity. When the
        new count reaches `max_failures`, also set the lockout key
        for `lock_duration`.
        """

    async def reset_verify_state(self, phone: str) -> None:
        """Drop the failure counter + lockout key for `phone`. Called
        on a successful verify so a legitimate user who finally gets
        the right code is immediately unblocked.
        """


class InMemoryRateLimiter:
    """Dict-backed limiter with explicit expiry checks.

    Not thread-safe — single uvicorn worker assumption holds for v0
    (same constraint as `InMemoryCodeStore`). Lazy eviction: each
    `get_*` call drops the entry if its window is past, so we don't
    need a sweeper.
    """

    def __init__(self) -> None:
        self._cooldowns: dict[str, datetime] = {}
        # phone → (failure_count, expires_at)
        self._fails: dict[str, tuple[int, datetime]] = {}
        self._locks: dict[str, datetime] = {}

    async def get_send_cooldown_seconds(self, phone: str) -> int | None:
        return self._seconds_left(self._cooldowns, phone)

    async def mark_send(self, phone: str, *, cooldown: timedelta) -> None:
        self._cooldowns[phone] = datetime.now(UTC) + cooldown

    async def get_verify_lock_seconds(self, phone: str) -> int | None:
        return self._seconds_left(self._locks, phone)

    async def record_verify_failure(
        self,
        phone: str,
        *,
        max_failures: int,
        lock_duration: timedelta,
    ) -> int:
        now = datetime.now(UTC)
        entry = self._fails.get(phone)
        # Either start a new window (no prior entry, or window expired)
        # or extend the existing count. We do NOT extend the expiry
        # on each failure — the window is anchored at the first failure
        # so an attacker can't push the lockout indefinitely far away
        # by just slowing down their guesses.
        if entry is None or now >= entry[1]:
            new_count = 1
            expires_at = now + lock_duration
        else:
            new_count = entry[0] + 1
            expires_at = entry[1]
        self._fails[phone] = (new_count, expires_at)
        if new_count >= max_failures:
            self._locks[phone] = expires_at
        return new_count

    async def reset_verify_state(self, phone: str) -> None:
        self._fails.pop(phone, None)
        self._locks.pop(phone, None)

    @staticmethod
    def _seconds_left(store: dict[str, datetime], phone: str) -> int | None:
        entry = store.get(phone)
        if entry is None:
            return None
        now = datetime.now(UTC)
        if now >= entry:
            store.pop(phone, None)
            return None
        # Round up so a sub-second remaining surfaces as 1, never 0 —
        # a Retry-After of 0 would tell the client to retry immediately
        # which defeats the cooldown.
        remaining = (entry - now).total_seconds()
        return max(1, int(remaining) + (1 if remaining % 1 > 0 else 0))


class RedisRateLimiter:
    """Redis-backed limiter.

    Uses `SET … EX` for atomic write+TTL on the cooldown / lock keys —
    no risk of a key lingering past its TTL because the application
    forgot to evict. The failure counter uses `INCR` + `EXPIRE … NX`
    in a transaction so the TTL is set exactly once (on the first
    failure) and the window doesn't slide on later increments.

    Phone numbers are namespaced under `auth:rl:*` (see module-level
    `_*_KEY_PREFIX` constants) so a future limiter on the same Redis
    can't collide with these keys.
    """

    def __init__(self, client: Redis) -> None:
        self._client = client

    async def get_send_cooldown_seconds(self, phone: str) -> int | None:
        return await self._ttl_or_none(_send_key(phone))

    async def mark_send(self, phone: str, *, cooldown: timedelta) -> None:
        await self._client.set(
            _send_key(phone),
            "1",
            ex=int(cooldown.total_seconds()),
        )

    async def get_verify_lock_seconds(self, phone: str) -> int | None:
        return await self._ttl_or_none(_lock_key(phone))

    async def record_verify_failure(
        self,
        phone: str,
        *,
        max_failures: int,
        lock_duration: timedelta,
    ) -> int:
        seconds = int(lock_duration.total_seconds())
        fails_key = _fails_key(phone)
        # Pipeline: atomically INCR and conditionally set TTL. `EXPIRE
        # … NX` only sets the TTL if the key has no TTL yet — anchors
        # the window at the first failure (matches the in-memory impl).
        async with self._client.pipeline(transaction=True) as pipe:
            pipe.incr(fails_key)
            pipe.expire(fails_key, seconds, nx=True)
            results = await pipe.execute()
        new_count = int(results[0])
        if new_count >= max_failures:
            await self._client.set(_lock_key(phone), "1", ex=seconds)
        return new_count

    async def reset_verify_state(self, phone: str) -> None:
        await self._client.delete(_fails_key(phone), _lock_key(phone))

    async def _ttl_or_none(self, key: str) -> int | None:
        # `TTL` returns -2 for missing key, -1 for key with no expiry,
        # otherwise positive seconds. We treat both negatives as "not
        # rate limited".
        ttl = await self._client.ttl(key)
        return int(ttl) if ttl > 0 else None


def _send_key(phone: str) -> str:
    return f"{_SEND_KEY_PREFIX}{phone}"


def _fails_key(phone: str) -> str:
    return f"{_FAILS_KEY_PREFIX}{phone}"


def _lock_key(phone: str) -> str:
    return f"{_LOCK_KEY_PREFIX}{phone}"


__all__ = [
    "MAX_VERIFY_FAILURES",
    "SEND_COOLDOWN",
    "VERIFY_LOCK_DURATION",
    "InMemoryRateLimiter",
    "RateLimited",
    "RateLimiter",
    "RedisRateLimiter",
]
