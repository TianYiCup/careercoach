"""Session lifecycle repository.

v0 ships in-memory only — keeps tests deterministic without needing a
running postgres. The DB-backed implementation drops into the same
factory slot in the next PR (4b), once SSE streaming has a turns
table to write into.

Naming note: distinct from `SessionScoreRepository` in the sharecards
package, which holds rendered-card-shaped data (`SessionCardData`).
`SessionRepository` here owns the bare session-row lifecycle.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import UTC, datetime
from typing import Protocol, runtime_checkable


@dataclass(frozen=True)
class SessionRecord:
    """Immutable snapshot of a session row.

    All mutations (e.g. `mark_ended`) return a new instance so callers
    never accidentally hold a stale view of the lifecycle state.
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


def utcnow() -> datetime:
    """Centralized UTC clock so tests can monkeypatch in one place."""
    return datetime.now(UTC)
