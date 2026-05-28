"""Episodic-memory persistence — Character Engine L6.

In-memory store for tests / dev-without-docker, SQLAlchemy store for
deployed envs; the `__init__.py` factory picks one via
`settings.memory_repo_backend`. Mirrors the weakness / profile repos.

`record` is an upsert keyed on (user_id, scenario_id): the first finished
session in a scenario inserts the row (visit_count 1), each later one
increments visit_count and overwrites the latest result + takeaway.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol, runtime_checkable

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.models.session_episode import SessionEpisode


@dataclass(frozen=True)
class EpisodeRecord:
    """Immutable snapshot of the opponent's memory of one (user, scenario)."""

    id: str
    user_id: str
    scenario_id: str
    visit_count: int
    last_result: str
    last_takeaway: str
    last_seen: datetime


@runtime_checkable
class EpisodeRepository(Protocol):
    """Persistence seam — both InMemory and Postgres impls below."""

    async def get(self, *, user_id: str, scenario_id: str) -> EpisodeRecord | None:
        """The user's latest episode for this scenario, or None if they've
        never finished a session here."""
        ...

    async def record(
        self,
        *,
        user_id: str,
        scenario_id: str,
        result: str,
        takeaway: str,
        now: datetime,
        fresh_id: str,
    ) -> None:
        """Upsert (user_id, scenario_id): insert on first finish
        (visit_count 1, using `fresh_id`), else increment visit_count and
        overwrite the latest result + takeaway."""
        ...


class InMemoryEpisodeRepository:
    """Dict-backed store keyed on (user_id, scenario_id)."""

    def __init__(self) -> None:
        self._store: dict[tuple[str, str], EpisodeRecord] = {}

    async def get(self, *, user_id: str, scenario_id: str) -> EpisodeRecord | None:
        return self._store.get((user_id, scenario_id))

    async def record(
        self,
        *,
        user_id: str,
        scenario_id: str,
        result: str,
        takeaway: str,
        now: datetime,
        fresh_id: str,
    ) -> None:
        key = (user_id, scenario_id)
        existing = self._store.get(key)
        if existing is None:
            self._store[key] = EpisodeRecord(
                id=fresh_id,
                user_id=user_id,
                scenario_id=scenario_id,
                visit_count=1,
                last_result=result,
                last_takeaway=takeaway,
                last_seen=now,
            )
            return
        self._store[key] = EpisodeRecord(
            id=existing.id,
            user_id=user_id,
            scenario_id=scenario_id,
            visit_count=existing.visit_count + 1,
            last_result=result,
            last_takeaway=takeaway,
            last_seen=now,
        )


class PostgresEpisodeRepository:
    """SQLAlchemy-backed implementation. `record` does a get-then-update-
    or-insert in one transaction so concurrent session-ends on the same
    scenario accumulate instead of tripping the unique constraint."""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def get(self, *, user_id: str, scenario_id: str) -> EpisodeRecord | None:
        async with self._session_factory() as session:
            stmt = select(SessionEpisode).where(
                SessionEpisode.user_id == user_id,
                SessionEpisode.scenario_id == scenario_id,
            )
            row = (await session.execute(stmt)).scalar_one_or_none()
            return _model_to_record(row) if row is not None else None

    async def record(
        self,
        *,
        user_id: str,
        scenario_id: str,
        result: str,
        takeaway: str,
        now: datetime,
        fresh_id: str,
    ) -> None:
        async with self._session_factory() as session, session.begin():
            stmt = select(SessionEpisode).where(
                SessionEpisode.user_id == user_id,
                SessionEpisode.scenario_id == scenario_id,
            )
            row = (await session.execute(stmt)).scalar_one_or_none()
            if row is None:
                session.add(
                    SessionEpisode(
                        id=fresh_id,
                        user_id=user_id,
                        scenario_id=scenario_id,
                        visit_count=1,
                        last_result=result,
                        last_takeaway=takeaway,
                        last_seen=now,
                    )
                )
            else:
                row.visit_count += 1
                row.last_result = result
                row.last_takeaway = takeaway
                row.last_seen = now


def _model_to_record(row: SessionEpisode) -> EpisodeRecord:
    return EpisodeRecord(
        id=row.id,
        user_id=row.user_id,
        scenario_id=row.scenario_id,
        visit_count=row.visit_count,
        last_result=row.last_result,
        last_takeaway=row.last_takeaway,
        last_seen=row.last_seen,
    )
