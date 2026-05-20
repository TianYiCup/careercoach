"""Streak persistence layer.

In-memory store for tests / dev-without-docker, SQLAlchemy store for
deployed envs. The `__init__.py` factory picks one via
`settings.streak_repo_backend` — same precedent as `vibe_repo_backend`.

`upsert` is keyed on the PK `user_id`: a user has exactly one streak
row, advanced in place.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Protocol, runtime_checkable

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.models.streak import Streak


@dataclass(frozen=True)
class StreakRecord:
    """Immutable snapshot of one user's streak."""

    user_id: str
    current_days: int
    max_days: int
    last_active_date: date


@runtime_checkable
class StreakRepository(Protocol):
    """Persistence seam — both InMemory and Postgres impls below."""

    async def get(self, user_id: str) -> StreakRecord | None: ...

    async def upsert(self, record: StreakRecord) -> None:
        """Insert or replace the streak row for `record.user_id`."""
        ...


class InMemoryStreakRepository:
    """Dict-backed store keyed on user_id. Single uvicorn worker is the
    only writer in v0, so no locks."""

    def __init__(self) -> None:
        self._store: dict[str, StreakRecord] = {}

    async def get(self, user_id: str) -> StreakRecord | None:
        return self._store.get(user_id)

    async def upsert(self, record: StreakRecord) -> None:
        self._store[record.user_id] = record


class PostgresStreakRepository:
    """SQLAlchemy-backed implementation. `upsert` does a PK get then
    update-or-insert in one transaction."""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def get(self, user_id: str) -> StreakRecord | None:
        async with self._session_factory() as session:
            row = await session.get(Streak, user_id)
            return None if row is None else _model_to_record(row)

    async def upsert(self, record: StreakRecord) -> None:
        async with self._session_factory() as session, session.begin():
            row = await session.get(Streak, record.user_id)
            if row is None:
                session.add(_record_to_model(record))
            else:
                row.current_days = record.current_days
                row.max_days = record.max_days
                row.last_active_date = record.last_active_date


def _record_to_model(record: StreakRecord) -> Streak:
    return Streak(
        user_id=record.user_id,
        current_days=record.current_days,
        max_days=record.max_days,
        last_active_date=record.last_active_date,
    )


def _model_to_record(row: Streak) -> StreakRecord:
    return StreakRecord(
        user_id=row.user_id,
        current_days=row.current_days,
        max_days=row.max_days,
        last_active_date=row.last_active_date,
    )
