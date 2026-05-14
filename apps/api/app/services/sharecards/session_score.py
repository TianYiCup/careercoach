"""Score store backing both share-card rendering and weekly/wrapped windows.

Two implementations:
  * `InMemorySessionScoreRepository` — dict-backed; default for dev
    and tests so docker isn't required.
  * `PostgresSessionScoreRepository` — writes to the `session_scores`
    table when `sharecards_score_repo_backend=postgres`.

Why a singleton factory: the session service WRITES to this store on
`/end` and the share-card service READS from it on
`/sharecards/session/{id}`. They must see the same data, so both go
through `get_session_score_repository()` and rely on its `@lru_cache`
to hand back the one instance.

`list_for_user` lives here (rather than in a separate aggregator) so
the same data plane backs three card surfaces — session, weekly, and
wrapped — without dragging in a duplicate read path. The in-memory
implementation stamps each `add()` with `datetime.now(UTC)`; the
postgres impl uses the `created_at` server default on the row.
"""

from __future__ import annotations

from datetime import UTC, datetime
from functools import lru_cache
from typing import Protocol, runtime_checkable

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.config import get_settings
from app.db.session import async_session_factory
from app.models.session_score import SessionScore as SessionScoreRow
from app.services.sharecards.types import SessionCardData


@runtime_checkable
class SessionScoreRepository(Protocol):
    """Resolves session ids → renderer payloads.

    `get` returns `None` when the session doesn't exist — the service
    maps that to 404 at the route layer. `list_for_user` returns an
    empty list (never `None`) when no sessions match the window so the
    weekly / wrapped aggregators can branch on `len(...)`.

    `add` is async to match the postgres-backed impl's natural shape.
    The in-memory impl just `await`-noops, but keeping the signature
    uniform avoids a per-impl call-site fork at the service layer.
    """

    async def add(self, session_id: str, data: SessionCardData) -> None: ...

    async def get(self, session_id: str, *, user_id: str) -> SessionCardData | None: ...

    async def list_for_user(
        self,
        user_id: str,
        *,
        since: datetime | None = None,
        until: datetime | None = None,
    ) -> list[SessionCardData]: ...


class InMemorySessionScoreRepository:
    """Dict-backed store for dev + tests. Not thread-safe by design —
    a single uvicorn worker is the only writer in v0.

    Each `add()` is stamped with `datetime.now(UTC)` so `list_for_user`
    can answer weekly / wrapped time-window queries. The user_id arg is
    accepted on the read side for protocol parity but v0 doesn't enforce
    per-user scoping — the DB-backed impl will."""

    def __init__(self, seed: dict[str, SessionCardData] | None = None) -> None:
        now = datetime.now(UTC)
        self._store: dict[str, SessionCardData] = dict(seed or {})
        # Seeded rows share `now` as their stamp so test fixtures behave
        # deterministically; real writes get the real time.
        self._stamps: dict[str, datetime] = dict.fromkeys(self._store, now)

    async def add(self, session_id: str, data: SessionCardData) -> None:
        self._store[session_id] = data
        self._stamps[session_id] = datetime.now(UTC)

    async def get(self, session_id: str, *, user_id: str) -> SessionCardData | None:
        _ = user_id  # v0 ignores user scoping; later repo enforces it
        return self._store.get(session_id)

    async def list_for_user(
        self,
        user_id: str,
        *,
        since: datetime | None = None,
        until: datetime | None = None,
    ) -> list[SessionCardData]:
        _ = user_id  # v0 ignores user scoping
        # Returned in insertion order so weekly + wrapped narrative can
        # pick "most recent" by indexing from the tail.
        result: list[SessionCardData] = []
        for session_id, data in self._store.items():
            stamped = self._stamps.get(session_id)
            if stamped is None:
                continue
            if since is not None and stamped < since:
                continue
            if until is not None and stamped >= until:
                continue
            result.append(data)
        return result


class PostgresSessionScoreRepository:
    """SQLAlchemy-backed score store.

    `add` is upsert-on-PK via `session.merge()` so an idempotent
    re-end of the same session (after a race or retry) updates the
    cached score instead of crashing on a PK conflict.

    `list_for_user` filters by `created_at` window for the weekly /
    wrapped aggregators; user_id is currently ignored (anon mode) but
    accepted on the signature so callers don't change when hard auth
    flips and we add a user_id column to the table.
    """

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def add(self, session_id: str, data: SessionCardData) -> None:
        async with self._session_factory() as session, session.begin():
            await session.merge(
                SessionScoreRow(
                    session_id=session_id,
                    scenario_title=data.scenario_title,
                    persona_title=data.persona_title,
                    aura=data.aura,
                    logic=data.logic,
                    emotion=data.emotion,
                    professionalism=data.professionalism,
                    goal_achieve=data.goal_achieve,
                    result=data.result,
                    highlights=data.highlights,
                )
            )

    async def get(self, session_id: str, *, user_id: str) -> SessionCardData | None:
        _ = user_id  # v0 ignores user scoping
        async with self._session_factory() as session:
            row = await session.get(SessionScoreRow, session_id)
            if row is None:
                return None
            return _row_to_data(row)

    async def list_for_user(
        self,
        user_id: str,
        *,
        since: datetime | None = None,
        until: datetime | None = None,
    ) -> list[SessionCardData]:
        _ = user_id  # v0 ignores user scoping; column lands with hard auth
        stmt = select(SessionScoreRow)
        if since is not None:
            stmt = stmt.where(SessionScoreRow.created_at >= since)
        if until is not None:
            stmt = stmt.where(SessionScoreRow.created_at < until)
        stmt = stmt.order_by(SessionScoreRow.created_at.asc())

        async with self._session_factory() as session:
            result = await session.execute(stmt)
            rows = result.scalars().all()
        return [_row_to_data(row) for row in rows]


def _row_to_data(row: SessionScoreRow) -> SessionCardData:
    return SessionCardData(
        scenario_title=row.scenario_title,
        persona_title=row.persona_title,
        aura=row.aura,
        logic=row.logic,
        emotion=row.emotion,
        professionalism=row.professionalism,
        goal_achieve=row.goal_achieve,
        result=row.result,  # type: ignore[arg-type]
        highlights=row.highlights,
    )


@lru_cache(maxsize=1)
def get_session_score_repository() -> SessionScoreRepository:
    """Process-wide singleton; backend chosen via settings."""
    backend = get_settings().sharecards_score_repo_backend
    if backend == "postgres":
        return PostgresSessionScoreRepository(async_session_factory)
    return InMemorySessionScoreRepository()
