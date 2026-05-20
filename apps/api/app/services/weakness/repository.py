"""Weakness-profile persistence layer.

In-memory store for tests / dev-without-docker, SQLAlchemy store for
deployed envs. The `__init__.py` factory picks one via
`settings.weakness_repo_backend` — same precedent as `vibe_repo_backend`.

`increment` is an upsert keyed on (user_id, tag): the first hit on a
tag inserts the row, later hits accumulate `frequency`. `frequency` is
floored at 0 — a negative delta ("improved") can't drive it below zero.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol, runtime_checkable

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.models.weakness import Weakness


@dataclass(frozen=True)
class WeaknessRecord:
    """Immutable snapshot of one tracked weakness."""

    id: str
    user_id: str
    tag: str
    frequency: int
    last_seen: datetime


@runtime_checkable
class WeaknessRepository(Protocol):
    """Persistence seam — both InMemory and Postgres impls below."""

    async def list_for_user(self, user_id: str) -> list[WeaknessRecord]:
        """All of the user's weaknesses, highest `frequency` first."""
        ...

    async def increment(
        self,
        *,
        user_id: str,
        tag: str,
        delta: int,
        now: datetime,
        fresh_id: str,
    ) -> None:
        """Add `delta` to the (user_id, tag) counter — insert on first
        hit (using `fresh_id`), accumulate after. Floors at 0."""
        ...


def _floor0(value: int) -> int:
    return max(0, value)


class InMemoryWeaknessRepository:
    """Dict-backed store keyed on (user_id, tag)."""

    def __init__(self) -> None:
        self._store: dict[tuple[str, str], WeaknessRecord] = {}

    async def list_for_user(self, user_id: str) -> list[WeaknessRecord]:
        rows = [r for (uid, _tag), r in self._store.items() if uid == user_id]
        return sorted(rows, key=lambda r: r.frequency, reverse=True)

    async def increment(
        self,
        *,
        user_id: str,
        tag: str,
        delta: int,
        now: datetime,
        fresh_id: str,
    ) -> None:
        key = (user_id, tag)
        existing = self._store.get(key)
        if existing is None:
            self._store[key] = WeaknessRecord(
                id=fresh_id,
                user_id=user_id,
                tag=tag,
                frequency=_floor0(delta),
                last_seen=now,
            )
            return
        self._store[key] = WeaknessRecord(
            id=existing.id,
            user_id=user_id,
            tag=tag,
            frequency=_floor0(existing.frequency + delta),
            last_seen=now,
        )


class PostgresWeaknessRepository:
    """SQLAlchemy-backed implementation. `increment` does a get-then-
    update-or-insert in one transaction so concurrent session-ends on
    the same tag accumulate instead of tripping the unique constraint."""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def list_for_user(self, user_id: str) -> list[WeaknessRecord]:
        async with self._session_factory() as session:
            stmt = (
                select(Weakness)
                .where(Weakness.user_id == user_id)
                .order_by(Weakness.frequency.desc())
            )
            rows = (await session.execute(stmt)).scalars().all()
            return [_model_to_record(row) for row in rows]

    async def increment(
        self,
        *,
        user_id: str,
        tag: str,
        delta: int,
        now: datetime,
        fresh_id: str,
    ) -> None:
        async with self._session_factory() as session, session.begin():
            stmt = select(Weakness).where(
                Weakness.user_id == user_id,
                Weakness.tag == tag,
            )
            row = (await session.execute(stmt)).scalar_one_or_none()
            if row is None:
                session.add(
                    Weakness(
                        id=fresh_id,
                        user_id=user_id,
                        tag=tag,
                        frequency=_floor0(delta),
                        last_seen=now,
                    )
                )
            else:
                row.frequency = _floor0(row.frequency + delta)
                row.last_seen = now


def _model_to_record(row: Weakness) -> WeaknessRecord:
    return WeaknessRecord(
        id=row.id,
        user_id=row.user_id,
        tag=row.tag,
        frequency=row.frequency,
        last_seen=row.last_seen,
    )
