"""Session lifecycle repository.

Two implementations live here: the in-memory store that backs unit
tests and dev runs without a docker stack, and the postgres-backed
store wired to `app.db.async_session_factory` for production. The
factory in `__init__.py` picks one based on `settings.sessions_repo_backend`.

Naming note: distinct from `SessionScoreRepository` in the sharecards
package, which holds rendered-card-shaped data (`SessionCardData`).
`SessionRepository` here owns the bare session-row lifecycle.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from typing import Protocol, runtime_checkable

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.models.session import Session
from app.services.scenarios.character_vector import CharacterVector


@dataclass(frozen=True)
class SessionRecord:
    """Immutable snapshot of a session row.

    All mutations (e.g. `mark_ended`) return a new instance so callers
    never accidentally hold a stale view of the lifecycle state.

    `mood_vector` (PR-L3) is the live 6-dim opponent state: starts equal
    to the scenario's static `character_vector` and shifts each turn as
    the MoodArbiter reads what the user just said. Defaults to neutral
    so a session created before L3 (e.g. in a unit test) still satisfies
    the dataclass and the prompt builder's L1 descriptor path stays valid.
    """

    session_id: str
    user_id: str
    mode: str
    scenario_id: str
    persona_id: str
    user_goal: str
    status: str  # 'active' | 'ended'
    created_at: datetime
    ended_at: datetime | None = None
    mood_vector: "CharacterVector" = field(  # noqa: UP037 — quoted to keep import below the class
        default_factory=lambda: CharacterVector.neutral()
    )


@runtime_checkable
class SessionRepository(Protocol):
    """Persistence seam — both `InMemorySessionRepository` and the
    upcoming SQL-backed impl conform to this surface."""

    async def save(self, record: SessionRecord) -> None: ...

    async def get(self, session_id: str) -> SessionRecord | None: ...

    async def mark_ended(self, session_id: str, *, ended_at: datetime) -> SessionRecord:
        """Flip status active → ended and stamp `ended_at`.

        Raises `KeyError` if the session id isn't known.
        """


class InMemorySessionRepository:
    """Dict-backed store for dev + tests. Not thread-safe by design —
    a single uvicorn worker is the only writer in v0."""

    def __init__(self) -> None:
        self._store: dict[str, SessionRecord] = {}

    async def save(self, record: SessionRecord) -> None:
        self._store[record.session_id] = record

    async def get(self, session_id: str) -> SessionRecord | None:
        return self._store.get(session_id)

    async def mark_ended(self, session_id: str, *, ended_at: datetime) -> SessionRecord:
        existing = self._store.get(session_id)
        if existing is None:
            raise KeyError(session_id)
        updated = replace(existing, status="ended", ended_at=ended_at)
        self._store[session_id] = updated
        return updated


class PostgresSessionRepository:
    """SQLAlchemy-backed implementation.

    Each method opens its own short-lived `AsyncSession` so the
    repository stays stateless and can be safely shared across
    request handlers. Long transactions belong in the service layer,
    not here.

    Conflict semantics for `save()`: the protocol contract is
    upsert-on-PK (later writes overwrite earlier ones for the same
    session_id). We rely on `merge()` rather than `add()` so an
    accidental double-create doesn't 500.
    """

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def save(self, record: SessionRecord) -> None:
        async with self._session_factory() as session, session.begin():
            await session.merge(_record_to_model(record))

    async def get(self, session_id: str) -> SessionRecord | None:
        async with self._session_factory() as session:
            row = await session.get(Session, session_id)
            if row is None:
                return None
            return _model_to_record(row)

    async def mark_ended(self, session_id: str, *, ended_at: datetime) -> SessionRecord:
        async with self._session_factory() as session, session.begin():
            row = await session.get(Session, session_id)
            if row is None:
                raise KeyError(session_id)
            row.status = "ended"
            row.ended_at = ended_at
            # `commit()` happens implicitly via `session.begin()`'s context.
            # Re-read inside the same transaction so the returned record
            # reflects the post-commit state (created_at server_default
            # is resolved by now).
            return _model_to_record(row)


def _record_to_model(record: SessionRecord) -> Session:
    return Session(
        session_id=record.session_id,
        user_id=record.user_id,
        mode=record.mode,
        scenario_id=record.scenario_id,
        persona_id=record.persona_id,
        user_goal=record.user_goal,
        status=record.status,
        created_at=record.created_at,
        ended_at=record.ended_at,
        mood_vector=record.mood_vector.to_dict(),
    )


def _model_to_record(row: Session) -> SessionRecord:
    return SessionRecord(
        session_id=row.session_id,
        user_id=row.user_id,
        mode=row.mode,
        scenario_id=row.scenario_id,
        persona_id=row.persona_id,
        user_goal=row.user_goal,
        status=row.status,
        created_at=row.created_at,
        ended_at=row.ended_at,
        mood_vector=CharacterVector.from_dict(row.mood_vector),
    )


def utcnow() -> datetime:
    """Centralized UTC clock so tests can monkeypatch in one place."""
    return datetime.now(UTC)
