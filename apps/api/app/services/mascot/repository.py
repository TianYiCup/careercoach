"""Mascot-moment persistence layer — PRD §7.10.

A `MascotMoment` is one entry in 教练 K's per-session expression
timeline (`POST /v1/mascot/log` appends, `GET /v1/mascot/expression`
reads it back for Wrapped replay).

In-memory only, on purpose. PRD §7.10 calls these moments
"弱关联，可丢失" — a weak association that may be lost — so a process-
lifetime store is the right v1 backend, and there is no SQLAlchemy
model / migration. A `PostgresMascotMomentRepository` can land behind
this Protocol later if the data ever needs to outlive the process.

The store is keyed on `(user_id, session_id)`, with `user_id` always
taken from the caller's JWT. That makes the timeline owner-isolated by
construction: a request can only ever read or write moments under its
own `user_id`, so no session-ownership lookup is needed.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Literal, Protocol, runtime_checkable

# The 8 expressions 教练 K cycles through (PRD §6 `MascotMoment.expression`).
MascotExpression = Literal[
    "confident",
    "burning",
    "thinking",
    "shenfeng",
    "fanche",
    "integrate",
    "caring",
    "sleeping",
]

# A sandbox session is capped at 30 turns (`MAX_TURNS_PER_SESSION`) and
# K has 8 expressions, so a real timeline tops out well under 100
# entries. 200 leaves generous headroom while bounding what a
# misbehaving client can append into the process-lifetime store.
_MAX_MOMENTS_PER_SESSION = 200


@dataclass(frozen=True)
class MascotMomentRecord:
    """Immutable snapshot of one 教练 K expression switch."""

    id: str
    user_id: str
    session_id: str
    turn_idx: int
    expression: MascotExpression
    at: datetime


@runtime_checkable
class MascotMomentRepository(Protocol):
    """Persistence seam for the mascot expression timeline."""

    async def append(self, record: MascotMomentRecord) -> None:
        """Append one moment to its `(user_id, session_id)` timeline."""
        ...

    async def list_for_session(
        self, user_id: str, session_id: str
    ) -> tuple[MascotMomentRecord, ...]:
        """Return the caller's timeline for `session_id`, oldest first.

        An empty tuple for an unknown session — never a missing-key
        error; the route surfaces that as an empty timeline, not a 404.
        """
        ...


class InMemoryMascotMomentRepository:
    """Dict-backed store keyed on `(user_id, session_id)`. A single
    uvicorn worker is the only writer in v0, so no locks."""

    def __init__(self) -> None:
        self._store: dict[tuple[str, str], list[MascotMomentRecord]] = {}

    async def append(self, record: MascotMomentRecord) -> None:
        key = (record.user_id, record.session_id)
        timeline = self._store.setdefault(key, [])
        timeline.append(record)
        # Trim oldest-first so a runaway client can't grow the store
        # without bound; Wrapped replay only cares about a real
        # session's worth of moments anyway.
        if len(timeline) > _MAX_MOMENTS_PER_SESSION:
            del timeline[:-_MAX_MOMENTS_PER_SESSION]

    async def list_for_session(
        self, user_id: str, session_id: str
    ) -> tuple[MascotMomentRecord, ...]:
        # Append order is chronological — each `append` stamps `at` with
        # the current time — so insertion order is already `at`-ascending.
        return tuple(self._store.get((user_id, session_id), ()))
