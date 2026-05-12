"""How the share-card service looks up the session it's rendering.

Sessions don't land in the DB until the Sprint-2 LangGraph + scoring
work; this Protocol is the seam between today's stub service and that
future repository. PR ③ ships an `InMemorySessionScoreRepository` that
holds a small fixture map — enough to demo the route end-to-end and
write deterministic tests. The Sprint-2 PR drops a `DbSessionScoreRepository`
into the same factory slot.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from app.services.sharecards.types import SessionCardData


@runtime_checkable
class SessionScoreRepository(Protocol):
    """Resolves a session id to the data the renderer wants.

    Returns `None` when the session doesn't exist — the service maps
    that to 404 at the route layer.
    """

    async def get(self, session_id: str, *, user_id: str) -> SessionCardData | None: ...


class InMemorySessionScoreRepository:
    """Fixture-backed score lookup. Dev + tests only — never in production.

    Populate with `add()` (or construct with a seed dict) so tests can
    pin which session_ids resolve. v0 main wiring leaves the map empty
    — every call returns None, the route returns 404 until a Sprint-2
    repo is wired in.
    """

    def __init__(self, seed: dict[str, SessionCardData] | None = None) -> None:
        self._store: dict[str, SessionCardData] = dict(seed or {})

    def add(self, session_id: str, data: SessionCardData) -> None:
        self._store[session_id] = data

    async def get(self, session_id: str, *, user_id: str) -> SessionCardData | None:
        _ = user_id  # v0 ignores user scoping; Sprint-2 repo will enforce it
        return self._store.get(session_id)
