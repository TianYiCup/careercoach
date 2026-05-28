"""User strategy-stats persistence — Character Engine L5.

In-memory store for tests / dev-without-docker, SQLAlchemy store for
deployed envs; the `__init__.py` factory picks one via
`settings.profile_repo_backend`. Mirrors the weakness repository.

`record` is an upsert keyed on (user_id, strategy): the first hit on a
strategy inserts the row, later hits accumulate `count` and the matching
per-effect tally (`good` / `mixed` / `poor`).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol, runtime_checkable

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.models.user_strategy_stat import UserStrategyStat

# The three effect keys (mirror coach_strategy.EFFECT_LABELS). Kept here
# too so the repo can validate without importing the coach module.
_EFFECT_COLUMNS = ("good", "mixed", "poor")


@dataclass(frozen=True)
class StrategyStatRecord:
    """Immutable snapshot of one (user, strategy) stat row."""

    id: str
    user_id: str
    strategy: str
    count: int
    good: int
    mixed: int
    poor: int
    last_seen: datetime

    @property
    def win_rate(self) -> float:
        """good / count, in [0, 1]. 0.0 when the strategy was never
        observed (count 0) — avoids a divide-by-zero and reads as
        "no evidence it works"."""
        if self.count <= 0:
            return 0.0
        return self.good / self.count


@runtime_checkable
class ProfileRepository(Protocol):
    """Persistence seam — both InMemory and Postgres impls below."""

    async def list_for_user(self, user_id: str) -> list[StrategyStatRecord]:
        """All of the user's strategy stats, highest `count` first."""
        ...

    async def record(
        self,
        *,
        user_id: str,
        strategy: str,
        effect: str,
        now: datetime,
        fresh_id: str,
    ) -> None:
        """Increment (user_id, strategy): +1 to `count` and +1 to the
        `effect` tally. Insert on first hit (using `fresh_id`). Unknown
        `effect` is a no-op (defensive — the caller validates upstream)."""
        ...


class InMemoryProfileRepository:
    """Dict-backed store keyed on (user_id, strategy)."""

    def __init__(self) -> None:
        self._store: dict[tuple[str, str], StrategyStatRecord] = {}

    async def list_for_user(self, user_id: str) -> list[StrategyStatRecord]:
        rows = [r for (uid, _s), r in self._store.items() if uid == user_id]
        return sorted(rows, key=lambda r: r.count, reverse=True)

    async def record(
        self,
        *,
        user_id: str,
        strategy: str,
        effect: str,
        now: datetime,
        fresh_id: str,
    ) -> None:
        if effect not in _EFFECT_COLUMNS:
            return
        key = (user_id, strategy)
        existing = self._store.get(key)
        if existing is None:
            tallies = {col: (1 if col == effect else 0) for col in _EFFECT_COLUMNS}
            self._store[key] = StrategyStatRecord(
                id=fresh_id,
                user_id=user_id,
                strategy=strategy,
                count=1,
                last_seen=now,
                **tallies,
            )
            return
        bumped = {
            col: getattr(existing, col) + (1 if col == effect else 0) for col in _EFFECT_COLUMNS
        }
        self._store[key] = StrategyStatRecord(
            id=existing.id,
            user_id=user_id,
            strategy=strategy,
            count=existing.count + 1,
            last_seen=now,
            **bumped,
        )


class PostgresProfileRepository:
    """SQLAlchemy-backed implementation. `record` does a get-then-update-
    or-insert in one transaction so concurrent turns on the same strategy
    accumulate instead of tripping the unique constraint."""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def list_for_user(self, user_id: str) -> list[StrategyStatRecord]:
        async with self._session_factory() as session:
            stmt = (
                select(UserStrategyStat)
                .where(UserStrategyStat.user_id == user_id)
                .order_by(UserStrategyStat.count.desc())
            )
            rows = (await session.execute(stmt)).scalars().all()
            return [_model_to_record(row) for row in rows]

    async def record(
        self,
        *,
        user_id: str,
        strategy: str,
        effect: str,
        now: datetime,
        fresh_id: str,
    ) -> None:
        if effect not in _EFFECT_COLUMNS:
            return
        async with self._session_factory() as session, session.begin():
            stmt = select(UserStrategyStat).where(
                UserStrategyStat.user_id == user_id,
                UserStrategyStat.strategy == strategy,
            )
            row = (await session.execute(stmt)).scalar_one_or_none()
            if row is None:
                session.add(
                    UserStrategyStat(
                        id=fresh_id,
                        user_id=user_id,
                        strategy=strategy,
                        count=1,
                        good=1 if effect == "good" else 0,
                        mixed=1 if effect == "mixed" else 0,
                        poor=1 if effect == "poor" else 0,
                        last_seen=now,
                    )
                )
            else:
                row.count += 1
                setattr(row, effect, getattr(row, effect) + 1)
                row.last_seen = now


def _model_to_record(row: UserStrategyStat) -> StrategyStatRecord:
    return StrategyStatRecord(
        id=row.id,
        user_id=row.user_id,
        strategy=row.strategy,
        count=row.count,
        good=row.good,
        mixed=row.mixed,
        poor=row.poor,
        last_seen=row.last_seen,
    )
