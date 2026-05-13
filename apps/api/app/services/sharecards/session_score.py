"""How the share-card service looks up the session it's rendering.

Sessions don't land in the DB until PR 4b; this Protocol is the seam
between today's in-memory wiring and that future SQL repository. The
DB-backed PR drops a `DbSessionScoreRepository` into the same factory
slot (`get_session_score_repository`).

Why a singleton factory: as of PR 4a, the session service WRITES to
this store on `/end` and the share-card service READS from it on
`/sharecards/session/{id}`. They must see the same in-memory map, so
both go through `get_session_score_repository()` and rely on its
`@lru_cache` to hand back the one instance.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Protocol, runtime_checkable

from app.services.sharecards.types import SessionCardData


@runtime_checkable
class SessionScoreRepository(Protocol):
    """Resolves a session id to the data the renderer wants.

    Returns `None` when the session doesn't exist — the service maps
    that to 404 at the route layer.
    """

    def add(self, session_id: str, data: SessionCardData) -> None: ...

    async def get(self, session_id: str, *, user_id: str) -> SessionCardData | None: ...


class InMemorySessionScoreRepository:
    """Dict-backed store for dev + tests. Not thread-safe by design —
    a single uvicorn worker is the only writer in v0.

    Populate with `add()` (or construct with a seed dict) so tests can
    pin which session_ids resolve. The production wiring (PR 4a) wires
    the session service's `/end` flow as the only writer; reads come
    from the share-card service.
    """

    def __init__(self, seed: dict[str, SessionCardData] | None = None) -> None:
        self._store: dict[str, SessionCardData] = dict(seed or {})

    def add(self, session_id: str, data: SessionCardData) -> None:
        self._store[session_id] = data

    async def get(self, session_id: str, *, user_id: str) -> SessionCardData | None:
        _ = user_id  # v0 ignores user scoping; later repo enforces it
        return self._store.get(session_id)


@lru_cache(maxsize=1)
def get_session_score_repository() -> InMemorySessionScoreRepository:
    """Process-wide singleton — see module docstring for the rationale."""
    return InMemorySessionScoreRepository()
