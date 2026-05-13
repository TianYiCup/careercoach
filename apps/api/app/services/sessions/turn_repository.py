"""Per-session turn history store.

PR 4b ships in-memory only — keeps SSE flow tests deterministic and
free of DB dependencies. The SQL-backed implementation drops into the
same protocol slot when the turns table lands.

Why a turn table at all (v0)
----------------------------
The LangGraph state machine treats turn history as ephemeral — each
node receives the conversation so far as a list of Messages and the
graph doesn't persist anything between invocations. For SSE-driven
sandbox practice that's not enough: when a user fires the second turn,
we need the prior opponent reply + user line to seed the next LLM call,
and the `/end` aggregator (PR 4c) wants to see every turn's score.

So the repository holds one immutable `TurnRecord` per turn, ordered
by `created_at`. SessionState rebuilds from `list(session_id)` on each
turn invocation.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol, runtime_checkable

from app.agents.state import TurnScore


@dataclass(frozen=True)
class CoachHintTrio:
    """Three-tone coaching hint emitted on every turn (PRD §7.4 coach.hint).

    The shapes match `apps/web/src/api/v1/types.ts::SseEventFrame`'s
    coach.hint variant so the wire contract is direct.
    """

    safe: str
    aggressive: str
    humor: str


@dataclass(frozen=True)
class TurnRecord:
    """Immutable snapshot of one user turn through the full pipeline."""

    turn_id: str
    session_id: str
    user_content: str
    opponent_reply: str
    coach_hint: CoachHintTrio
    turn_score: TurnScore
    created_at: datetime


@runtime_checkable
class TurnRepository(Protocol):
    """Persistence seam — SQL-backed impl lands in a later PR."""

    async def append(self, record: TurnRecord) -> None: ...

    async def list_for_session(self, session_id: str) -> list[TurnRecord]: ...


class InMemoryTurnRepository:
    """Dict-of-list store. Not thread-safe by design — single uvicorn
    worker is the only writer in v0."""

    def __init__(self) -> None:
        self._store: dict[str, list[TurnRecord]] = {}

    async def append(self, record: TurnRecord) -> None:
        self._store.setdefault(record.session_id, []).append(record)

    async def list_for_session(self, session_id: str) -> list[TurnRecord]:
        # Returns a copy so callers can't mutate the canonical list.
        return list(self._store.get(session_id, []))
