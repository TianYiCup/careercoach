"""Per-session turn history store.

Two implementations live here: the in-memory store that backs unit
tests and dev runs without a docker stack, and the postgres-backed
store wired to `app.db.async_session_factory` for production. The
factory in `__init__.py` picks one based on `settings.sessions_repo_backend`.

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

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.agents.state import TurnScore, Verdict
from app.models.turn import Turn


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


class PostgresTurnRepository:
    """SQLAlchemy-backed implementation.

    Append is the hot path here — every /turns call writes exactly
    one row. We open a fresh AsyncSession per append rather than
    reusing one across the SSE lifecycle: the SSE handler is
    single-threaded inside one request, and an open transaction
    across a streaming response can hold a row lock for the full
    LLM round-trip.

    `list_for_session` orders by `created_at` so the aggregator's
    "most recent" semantics stay stable.
    """

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def append(self, record: TurnRecord) -> None:
        async with self._session_factory() as session, session.begin():
            session.add(_record_to_model(record))

    async def list_for_session(self, session_id: str) -> list[TurnRecord]:
        stmt = select(Turn).where(Turn.session_id == session_id).order_by(Turn.created_at.asc())
        async with self._session_factory() as session:
            result = await session.execute(stmt)
            rows = result.scalars().all()
        return [_model_to_record(row) for row in rows]


def _record_to_model(record: TurnRecord) -> Turn:
    return Turn(
        turn_id=record.turn_id,
        session_id=record.session_id,
        user_content=record.user_content,
        opponent_reply=record.opponent_reply,
        coach_hint_safe=record.coach_hint.safe,
        coach_hint_aggressive=record.coach_hint.aggressive,
        coach_hint_humor=record.coach_hint.humor,
        verdict=record.turn_score.verdict.value,
        rating=record.turn_score.rating,
        created_at=record.created_at,
    )


def _model_to_record(row: Turn) -> TurnRecord:
    return TurnRecord(
        turn_id=row.turn_id,
        session_id=row.session_id,
        user_content=row.user_content,
        opponent_reply=row.opponent_reply,
        coach_hint=CoachHintTrio(
            safe=row.coach_hint_safe,
            aggressive=row.coach_hint_aggressive,
            humor=row.coach_hint_humor,
        ),
        turn_score=TurnScore(verdict=Verdict(row.verdict), rating=row.rating),
        created_at=row.created_at,
    )
