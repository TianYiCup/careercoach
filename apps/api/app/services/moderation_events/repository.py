"""Read-side repository for `moderation_events` (A-43 foundation).

The write path is `app.services.moderation.event_sink.DbEventSink` —
that's the single chokepoint for hashing content + inserting an
audit row from `ModerationService.check`. This repo is the parallel
read path for the ops surface (`GET /v1/ops/moderation-events`).

Read-only on purpose. Backfilling or replaying events into the
audit log is a maintenance scenario we deliberately keep off this
seam: there's exactly one production writer (`DbEventSink`) and
that's how it stays. The InMemory impl exposes an `insert(...)` for
test seeding only — the Protocol omits it so route-side callers
can't accidentally bypass the sink (which guarantees content is
hashed, not stored raw).

Why a dataclass + separate Pydantic schema (same rationale as A-39):
storage shape and wire shape have different stability contracts;
keeping them in different files means a future rename in one
doesn't propagate to the other by accident.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol, runtime_checkable

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.models.moderation_event import ModerationEvent


@dataclass(frozen=True)
class ModerationEventRecord:
    """Immutable snapshot of one persisted moderation decision.

    `content_hash` is SHA-256 hex (always 64 chars) — the raw content
    is NEVER read out of the DB. Per `event_sink.py`, storing raw
    text would turn the audit log itself into a red-line corpus.
    """

    id: uuid.UUID
    user_id: str
    session_id: str | None
    content_hash: str
    content_length: int
    context: str
    verdict: str
    categories: tuple[str, ...]
    score: float
    backend: str
    trace_id: str
    created_at: datetime


@runtime_checkable
class ModerationEventRepository(Protocol):
    """Read-side seam — both InMemory and Postgres impls below.

    One method (`list_events`) is enough for A-43. Per-id lookup and
    aggregate-by-verdict can grow into the Protocol later if ops
    actually needs them; YAGNI for now.
    """

    async def list_events(
        self,
        *,
        since: datetime | None = None,
        until: datetime | None = None,
        user_id: str | None = None,
        verdict: str | None = None,
        limit: int = 50,
    ) -> list[ModerationEventRecord]: ...


class InMemoryModerationEventRepository:
    """List-backed store. Single uvicorn worker is the only reader in
    v0, so no locks. `insert(...)` is deliberately NOT on the Protocol
    — only test seeding uses it; production writes go through
    `DbEventSink` so content is hashed once at the boundary."""

    def __init__(self) -> None:
        self._records: list[ModerationEventRecord] = []

    async def insert(self, record: ModerationEventRecord) -> None:
        """Test-only seeder. Production never calls this — the
        DbEventSink writes directly via the model."""
        self._records.append(record)

    async def list_events(
        self,
        *,
        since: datetime | None = None,
        until: datetime | None = None,
        user_id: str | None = None,
        verdict: str | None = None,
        limit: int = 50,
    ) -> list[ModerationEventRecord]:
        matching = [
            rec
            for rec in self._records
            if _matches_filters(
                rec,
                since=since,
                until=until,
                user_id=user_id,
                verdict=verdict,
            )
        ]
        # Most-recent-first matches the Postgres ORDER BY contract so
        # callers see the same ranking regardless of backend.
        matching.sort(key=lambda r: r.created_at, reverse=True)
        return matching[:limit]


class PostgresModerationEventRepository:
    """SQLAlchemy-backed implementation. Single read query — Postgres
    uses the `ix_moderation_events_user_id` index when `user_id` is
    supplied (the common ops drill-down). For unfiltered global tails
    at v1 scale we'll need a `(created_at DESC)` index, but v0 daily
    volume (~hundreds of events) is well below where the sort cost
    matters."""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def list_events(
        self,
        *,
        since: datetime | None = None,
        until: datetime | None = None,
        user_id: str | None = None,
        verdict: str | None = None,
        limit: int = 50,
    ) -> list[ModerationEventRecord]:
        stmt = select(ModerationEvent).order_by(ModerationEvent.created_at.desc()).limit(limit)
        if since is not None:
            stmt = stmt.where(ModerationEvent.created_at >= since)
        if until is not None:
            stmt = stmt.where(ModerationEvent.created_at < until)
        if user_id is not None:
            stmt = stmt.where(ModerationEvent.user_id == user_id)
        if verdict is not None:
            stmt = stmt.where(ModerationEvent.verdict == verdict)
        async with self._session_factory() as session:
            rows = (await session.execute(stmt)).scalars().all()
        return [_model_to_record(row) for row in rows]


# --- helpers ---


def _matches_filters(
    record: ModerationEventRecord,
    *,
    since: datetime | None,
    until: datetime | None,
    user_id: str | None,
    verdict: str | None,
) -> bool:
    if since is not None and record.created_at < since:
        return False
    if until is not None and record.created_at >= until:
        return False
    if user_id is not None and record.user_id != user_id:
        return False
    return not (verdict is not None and record.verdict != verdict)


def _model_to_record(model: ModerationEvent) -> ModerationEventRecord:
    # `categories` is JSON-typed on the model (list of strings); we
    # freeze it into a tuple in the record so the dataclass stays
    # hashable / immutable in line with the LLMCallRecord pattern.
    categories = tuple(model.categories or ())
    return ModerationEventRecord(
        id=model.id,
        user_id=model.user_id,
        session_id=model.session_id,
        content_hash=model.content_hash,
        content_length=model.content_length,
        context=model.context,
        verdict=model.verdict,
        categories=categories,
        score=float(model.score),
        backend=model.backend,
        trace_id=model.trace_id,
        created_at=model.created_at,
    )
