"""How the share-card service looks up the sessions it's rendering.

Sessions don't land in the DB until a later PR; this Protocol is the
seam between today's in-memory wiring and that future SQL repository.
The DB-backed PR drops a `DbSessionScoreRepository` into the same
factory slot (`get_session_score_repository`).

Why a singleton factory: as of PR 4a, the session service WRITES to
this store on `/end` and the share-card service READS from it on
`/sharecards/session/{id}`. They must see the same in-memory map, so
both go through `get_session_score_repository()` and rely on its
`@lru_cache` to hand back the one instance.

`list_for_user` lives here (rather than in a separate aggregator) so
the same data plane backs three card surfaces — session, weekly, and
wrapped — without dragging in a duplicate read path. The in-memory
implementation stamps each `add()` with `datetime.now(UTC)` so weekly
+ wrapped can filter by window; the DB-backed impl will join against
`sessions.ended_at`.
"""

from __future__ import annotations

from datetime import UTC, datetime
from functools import lru_cache
from typing import Protocol, runtime_checkable

from app.services.sharecards.types import SessionCardData


@runtime_checkable
class SessionScoreRepository(Protocol):
    """Resolves session ids → renderer payloads.

    `get` returns `None` when the session doesn't exist — the service
    maps that to 404 at the route layer. `list_for_user` returns an
    empty list (never `None`) when no sessions match the window so the
    weekly / wrapped aggregators can branch on `len(...)`.
    """

    def add(self, session_id: str, data: SessionCardData) -> None: ...

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

    def add(self, session_id: str, data: SessionCardData) -> None:
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


@lru_cache(maxsize=1)
def get_session_score_repository() -> InMemorySessionScoreRepository:
    """Process-wide singleton — see module docstring for the rationale."""
    return InMemorySessionScoreRepository()
