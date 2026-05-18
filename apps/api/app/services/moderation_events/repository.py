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
from collections import Counter
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol, runtime_checkable

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.models.moderation_event import ModerationEvent

# Canonical verdict order — the user-facing Literal enum from
# `app.schemas.moderation` is also (allow, warn, redirect, block).
# We pin it explicitly here so `by_verdict` is always 4 entries in
# the same order regardless of which verdicts the window actually
# contains. Callers rendering a dashboard get a stable layout (and
# zero-counts surface honestly instead of going missing).
_VERDICT_ORDER: tuple[str, ...] = ("allow", "warn", "redirect", "block")


@dataclass(frozen=True)
class ModerationStatsTotals:
    """Headline counts across the window — what the rate dashboard
    builds on. Per-verdict counts are first-class fields (not a dict)
    because the four verdicts are part of the documented schema."""

    event_count: int
    allow_count: int
    warn_count: int
    redirect_count: int
    block_count: int

    @classmethod
    def zero(cls) -> ModerationStatsTotals:
        return cls(
            event_count=0,
            allow_count=0,
            warn_count=0,
            redirect_count=0,
            block_count=0,
        )


@dataclass(frozen=True)
class ModerationStatsBreakdownEntry:
    """One row in a by-something breakdown. Kept generic so the same
    dataclass backs by_verdict / by_context / by_category / by_backend
    without duplication, mirroring A-39's `LLMCallBreakdownEntry`."""

    key: str
    count: int


@dataclass(frozen=True)
class ModerationEventAggregate:
    """Roll-up across one window × optional user filter.

    `by_verdict` is sorted in the canonical order
    (allow, warn, redirect, block) so dashboards render the same
    columns regardless of which verdicts actually appeared. The
    other breakdowns are sorted by count desc (most-frequent first
    — the read most ops queries are looking for is "what's at the top").

    `by_category` counts EACH row once per category it carries (a row
    flagged with both `self_harm` and `violence` contributes to both
    buckets). That intentionally double-counts so the rate dashboard
    reads as "how often did category X fire", not "how many rows had
    exactly one category".
    """

    user_id: str | None
    since: datetime | None
    until: datetime | None
    totals: ModerationStatsTotals
    by_verdict: tuple[ModerationStatsBreakdownEntry, ...]
    by_context: tuple[ModerationStatsBreakdownEntry, ...]
    by_category: tuple[ModerationStatsBreakdownEntry, ...]
    by_backend: tuple[ModerationStatsBreakdownEntry, ...]


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

    async def aggregate(
        self,
        *,
        since: datetime | None = None,
        until: datetime | None = None,
        user_id: str | None = None,
    ) -> ModerationEventAggregate: ...


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

    async def aggregate(
        self,
        *,
        since: datetime | None = None,
        until: datetime | None = None,
        user_id: str | None = None,
    ) -> ModerationEventAggregate:
        matching = [
            rec
            for rec in self._records
            if _matches_filters(
                rec,
                since=since,
                until=until,
                user_id=user_id,
                verdict=None,
            )
        ]
        return _aggregate_records(matching, user_id=user_id, since=since, until=until)


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

    async def aggregate(
        self,
        *,
        since: datetime | None = None,
        until: datetime | None = None,
        user_id: str | None = None,
    ) -> ModerationEventAggregate:
        # Fetch matching rows, then aggregate in Python.
        #
        # Why not SQL GROUP BY: `categories` is a JSON list column;
        # bucketing it natively needs `jsonb_array_elements_text` (PG-only)
        # and divergent code paths for the sqlite-backed unit tests
        # the model intentionally stays portable for. At v0 volume
        # (~hundreds of events/day per ops query) the haul-and-group
        # cost is negligible. When per-tenant volume crosses ~100k
        # rows/query the verdict/context/backend GROUP BYs should
        # move to SQL while category aggregation stays in Python.
        stmt = select(ModerationEvent)
        if since is not None:
            stmt = stmt.where(ModerationEvent.created_at >= since)
        if until is not None:
            stmt = stmt.where(ModerationEvent.created_at < until)
        if user_id is not None:
            stmt = stmt.where(ModerationEvent.user_id == user_id)
        async with self._session_factory() as session:
            rows = (await session.execute(stmt)).scalars().all()
        records = [_model_to_record(row) for row in rows]
        return _aggregate_records(records, user_id=user_id, since=since, until=until)


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


def _aggregate_records(
    records: list[ModerationEventRecord],
    *,
    user_id: str | None,
    since: datetime | None,
    until: datetime | None,
) -> ModerationEventAggregate:
    """Build the rollup from a pre-filtered record list.

    Shared between InMemory and Postgres impls so a future fix to
    the aggregation semantics (counts, sort order, verdict ordering)
    lands in both backends at once.
    """
    verdict_counts: Counter[str] = Counter(r.verdict for r in records)
    context_counts: Counter[str] = Counter(r.context for r in records)
    backend_counts: Counter[str] = Counter(r.backend for r in records)
    # `categories` is a tuple per record; flat-iterate so a row tagged
    # with `(self_harm, violence)` contributes once to each bucket.
    # That's the "how often did category X fire" reading — see the
    # aggregate's docstring for why we don't dedupe.
    category_counts: Counter[str] = Counter(c for r in records for c in r.categories)

    totals = ModerationStatsTotals(
        event_count=len(records),
        allow_count=verdict_counts.get("allow", 0),
        warn_count=verdict_counts.get("warn", 0),
        redirect_count=verdict_counts.get("redirect", 0),
        block_count=verdict_counts.get("block", 0),
    )

    # by_verdict is pinned to (allow, warn, redirect, block) regardless
    # of which verdicts the window contains — gives dashboards a stable
    # 4-column layout where zero-counts surface honestly.
    by_verdict = tuple(
        ModerationStatsBreakdownEntry(key=v, count=verdict_counts.get(v, 0)) for v in _VERDICT_ORDER
    )

    return ModerationEventAggregate(
        user_id=user_id,
        since=since,
        until=until,
        totals=totals,
        by_verdict=by_verdict,
        by_context=_count_sorted(context_counts),
        by_category=_count_sorted(category_counts),
        by_backend=_count_sorted(backend_counts),
    )


def _count_sorted(counts: Counter[str]) -> tuple[ModerationStatsBreakdownEntry, ...]:
    """Most-frequent first, ties broken by key alphabetic for stable
    output (matters when the test suite asserts on the exact order)."""
    return tuple(
        ModerationStatsBreakdownEntry(key=key, count=count)
        for key, count in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))
    )


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
