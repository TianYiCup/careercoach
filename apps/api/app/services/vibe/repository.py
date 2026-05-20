"""Vibe-log persistence layer.

In-memory store for tests / dev-without-docker, SQLAlchemy store for
deployed envs. The `__init__.py` factory picks one via
`settings.vibe_repo_backend` — same precedent as `copilot_repo_backend`.

`set_today` is an upsert keyed on (user_id, logged_date): a second
check-in on the same day overwrites the mood rather than stacking
rows, matching the `(user_id, logged_date)` unique constraint. Both
impls keep the original row `id` on overwrite so the row's identity is
stable; only `vibe` + `created_at` move.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import date, datetime
from typing import Literal, Protocol, runtime_checkable

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.models.vibe_log import VibeLog

VibeType = Literal["fire", "tired", "anxious", "excited", "meh"]


@dataclass(frozen=True)
class VibeLogRecord:
    """Immutable snapshot of one vibe check-in."""

    id: str
    user_id: str
    vibe: VibeType
    logged_date: date
    created_at: datetime


@runtime_checkable
class VibeRepository(Protocol):
    """Persistence seam — both InMemory and Postgres impls below."""

    async def set_today(self, record: VibeLogRecord) -> None:
        """Upsert the check-in for `(record.user_id, record.logged_date)`."""
        ...

    async def get_for_date(self, user_id: str, logged_date: date) -> VibeLogRecord | None: ...


class InMemoryVibeRepository:
    """Dict-backed store keyed on (user_id, logged_date). Single uvicorn
    worker is the only writer in v0, so no locks."""

    def __init__(self) -> None:
        self._store: dict[tuple[str, date], VibeLogRecord] = {}

    async def set_today(self, record: VibeLogRecord) -> None:
        key = (record.user_id, record.logged_date)
        existing = self._store.get(key)
        if existing is not None:
            # Keep the original row id; only the mood + timestamp move.
            record = replace(record, id=existing.id)
        self._store[key] = record

    async def get_for_date(self, user_id: str, logged_date: date) -> VibeLogRecord | None:
        return self._store.get((user_id, logged_date))


class PostgresVibeRepository:
    """SQLAlchemy-backed implementation. `set_today` does a get-then-
    update-or-insert in one transaction so a re-POST on the same day
    overwrites the mood instead of tripping the unique constraint."""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def set_today(self, record: VibeLogRecord) -> None:
        async with self._session_factory() as session, session.begin():
            stmt = select(VibeLog).where(
                VibeLog.user_id == record.user_id,
                VibeLog.logged_date == record.logged_date,
            )
            row = (await session.execute(stmt)).scalar_one_or_none()
            if row is None:
                session.add(_record_to_model(record))
            else:
                # Overwrite the mood; keep the original id so the row's
                # identity stays stable across re-check-ins.
                row.vibe = record.vibe
                row.created_at = record.created_at

    async def get_for_date(self, user_id: str, logged_date: date) -> VibeLogRecord | None:
        async with self._session_factory() as session:
            stmt = select(VibeLog).where(
                VibeLog.user_id == user_id,
                VibeLog.logged_date == logged_date,
            )
            row = (await session.execute(stmt)).scalar_one_or_none()
            return None if row is None else _model_to_record(row)


def _record_to_model(record: VibeLogRecord) -> VibeLog:
    return VibeLog(
        id=record.id,
        user_id=record.user_id,
        vibe=record.vibe,
        logged_date=record.logged_date,
        created_at=record.created_at,
    )


def _model_to_record(row: VibeLog) -> VibeLogRecord:
    return VibeLogRecord(
        id=row.id,
        user_id=row.user_id,
        vibe=_coerce_vibe(row.vibe),
        logged_date=row.logged_date,
        created_at=row.created_at,
    )


# `vibe` is a plain String(16) so adding moods never needs an ALTER
# TYPE migration. This narrowing helper preserves the typed Literal at
# the boundary; an unexpected value (hand-edited row) raises rather
# than silently corrupting downstream typing.
def _coerce_vibe(raw: str) -> VibeType:
    if raw not in ("fire", "tired", "anxious", "excited", "meh"):
        raise ValueError(f"unknown vibe: {raw!r}")
    return raw  # type: ignore[return-value]
