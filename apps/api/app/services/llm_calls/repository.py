"""LLMCall persistence layer + aggregate-query primitives.

A-39 lays the foundation: a `Protocol` plus in-memory and Postgres
implementations, mirroring the `CopilotRepository` / `ModerationEvent`
shape used elsewhere in the codebase. Writer wiring (calling
`insert(record)` from observability hooks) is A-40; the ops-side
read endpoint that drives `aggregate_by_user` is A-41/A-42.

Why the aggregate types live here and not in a schemas/ module
--------------------------------------------------------------
`LLMCallAggregate` is the contract between the repo and any caller
(rollup endpoint, ad-hoc CLI, cron). It's not a wire schema (the
route layer can wrap it in a Pydantic `BaseModel` for API
serialization). Keeping it adjacent to the repo means the read
shape and the read code evolve together.
"""

from __future__ import annotations

import uuid
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Protocol, runtime_checkable

from sqlalchemy import Select, func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.models.llm_call import LLMCall


@dataclass(frozen=True)
class LLMCallRecord:
    """Immutable snapshot of one persisted LLM call.

    `id` is the row PK. Callers building a new record can pass
    `uuid.uuid4()` themselves — the model has a default but we let
    the caller see/log the id before insert (matches the
    `ModerationEvent` insert pattern at other sites).
    """

    id: uuid.UUID
    trace_id: str
    user_id: str
    surface: str
    model: str
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int
    created_at: datetime


@dataclass(frozen=True)
class LLMCallTotals:
    """Sum across the window — what the headline cost number is built on."""

    call_count: int
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int

    @classmethod
    def zero(cls) -> LLMCallTotals:
        return cls(call_count=0, prompt_tokens=0, completion_tokens=0, total_tokens=0)


@dataclass(frozen=True)
class LLMCallBreakdownEntry:
    """One row in a by-model or by-surface breakdown.

    `key` is the grouping value (e.g. `"deepseek-chat"` for by_model,
    `"sandbox"` for by_surface). Kept generic so the same dataclass
    backs both groupings without duplication.
    """

    key: str
    call_count: int
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int


@dataclass(frozen=True)
class LLMCallAggregate:
    """Roll-up across one user × one time window.

    Returned by `aggregate_by_user`. `since` / `until` echo the
    window the caller asked for so a downstream serializer can render
    them in the response without re-deriving (and so a `None` bound
    surfaces as `null` honestly — rather than a fabricated "epoch"
    that suggests we filtered).

    `by_model` / `by_surface` are sorted by `total_tokens` desc — the
    most expensive bucket first — because the rollup endpoint's
    primary read is "what's burning my budget right now".
    """

    user_id: str
    since: datetime | None
    until: datetime | None
    totals: LLMCallTotals
    by_model: tuple[LLMCallBreakdownEntry, ...]
    by_surface: tuple[LLMCallBreakdownEntry, ...]


@runtime_checkable
class LLMCallRepository(Protocol):
    """Persistence seam — both InMemory and Postgres impls below.

    Two methods are enough for A-39: one writer (`insert`) and one
    reader (`aggregate_by_user`). Per-trace lookup and recent-call
    listing can grow into the protocol later if the endpoint needs
    debug surface area; YAGNI for now.
    """

    async def insert(self, record: LLMCallRecord) -> None: ...

    async def aggregate_by_user(
        self,
        user_id: str,
        *,
        since: datetime | None = None,
        until: datetime | None = None,
    ) -> LLMCallAggregate: ...


class InMemoryLLMCallRepository:
    """List-backed store. Single uvicorn worker is the only writer in
    v0, so no locks. Records are stored immutably; aggregation is a
    Python sum + group-by (fine at v0 volumes — < 10k rows/day)."""

    def __init__(self) -> None:
        self._records: list[LLMCallRecord] = []

    async def insert(self, record: LLMCallRecord) -> None:
        self._records.append(record)

    async def aggregate_by_user(
        self,
        user_id: str,
        *,
        since: datetime | None = None,
        until: datetime | None = None,
    ) -> LLMCallAggregate:
        matching = [
            rec
            for rec in self._records
            if rec.user_id == user_id and _within_window(rec.created_at, since, until)
        ]
        totals = _sum_totals(matching)
        by_model = _breakdown(matching, key_fn=lambda r: r.model)
        by_surface = _breakdown(matching, key_fn=lambda r: r.surface)
        return LLMCallAggregate(
            user_id=user_id,
            since=since,
            until=until,
            totals=totals,
            by_model=by_model,
            by_surface=by_surface,
        )


class PostgresLLMCallRepository:
    """SQLAlchemy-backed implementation.

    `insert` opens its own short transaction (matches `ModerationEvent`
    insert pattern). `aggregate_by_user` issues three queries —
    totals, by-model, by-surface — rather than one big GROUP BY
    GROUPING SETS, because the rollup is called rarely (ops-only)
    and three plain queries debug far easier than the single fancy one.
    The per-user index on `user_id` keeps each query cheap.
    """

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def insert(self, record: LLMCallRecord) -> None:
        async with self._session_factory() as session, session.begin():
            session.add(_record_to_model(record))

    async def aggregate_by_user(
        self,
        user_id: str,
        *,
        since: datetime | None = None,
        until: datetime | None = None,
    ) -> LLMCallAggregate:
        async with self._session_factory() as session:
            totals = await self._query_totals(session, user_id, since, until)
            by_model = await self._query_breakdown(
                session, user_id, since, until, group_col=LLMCall.model
            )
            by_surface = await self._query_breakdown(
                session, user_id, since, until, group_col=LLMCall.surface
            )
        return LLMCallAggregate(
            user_id=user_id,
            since=since,
            until=until,
            totals=totals,
            by_model=by_model,
            by_surface=by_surface,
        )

    async def _query_totals(
        self,
        session: AsyncSession,
        user_id: str,
        since: datetime | None,
        until: datetime | None,
    ) -> LLMCallTotals:
        stmt = select(
            func.count(LLMCall.id),
            func.coalesce(func.sum(LLMCall.prompt_tokens), 0),
            func.coalesce(func.sum(LLMCall.completion_tokens), 0),
            func.coalesce(func.sum(LLMCall.total_tokens), 0),
        ).where(LLMCall.user_id == user_id)
        stmt = _apply_window(stmt, since, until)
        row = (await session.execute(stmt)).one()
        return LLMCallTotals(
            call_count=int(row[0]),
            prompt_tokens=int(row[1]),
            completion_tokens=int(row[2]),
            total_tokens=int(row[3]),
        )

    async def _query_breakdown(
        self,
        session: AsyncSession,
        user_id: str,
        since: datetime | None,
        until: datetime | None,
        *,
        group_col: Any,
    ) -> tuple[LLMCallBreakdownEntry, ...]:
        # `group_col` is typed `Any` because SQLAlchemy's `InstrumentedAttribute`
        # isn't in the public type namespace — we only ever pass `LLMCall.model`
        # / `LLMCall.surface` so callers can't actually misuse it.
        total = func.coalesce(func.sum(LLMCall.total_tokens), 0)
        stmt = (
            select(
                group_col,
                func.count(LLMCall.id),
                func.coalesce(func.sum(LLMCall.prompt_tokens), 0),
                func.coalesce(func.sum(LLMCall.completion_tokens), 0),
                total,
            )
            .where(LLMCall.user_id == user_id)
            .group_by(group_col)
            .order_by(total.desc())
        )
        stmt = _apply_window(stmt, since, until)
        rows = (await session.execute(stmt)).all()
        return tuple(
            LLMCallBreakdownEntry(
                key=str(row[0]),
                call_count=int(row[1]),
                prompt_tokens=int(row[2]),
                completion_tokens=int(row[3]),
                total_tokens=int(row[4]),
            )
            for row in rows
        )


# --- helpers ---


def _within_window(
    created_at: datetime,
    since: datetime | None,
    until: datetime | None,
) -> bool:
    if since is not None and created_at < since:
        return False
    return not (until is not None and created_at >= until)


def _sum_totals(records: list[LLMCallRecord]) -> LLMCallTotals:
    if not records:
        return LLMCallTotals.zero()
    return LLMCallTotals(
        call_count=len(records),
        prompt_tokens=sum(r.prompt_tokens for r in records),
        completion_tokens=sum(r.completion_tokens for r in records),
        total_tokens=sum(r.total_tokens for r in records),
    )


def _breakdown(
    records: list[LLMCallRecord],
    *,
    key_fn: Any,
) -> tuple[LLMCallBreakdownEntry, ...]:
    # `key_fn` is typed `Any` instead of `Callable[[LLMCallRecord], str]`
    # to keep the helper signature symmetric with Postgres's `group_col`
    # (also `Any`) — both paths only ever receive call-site-internal
    # values we control.
    buckets: dict[str, list[LLMCallRecord]] = defaultdict(list)
    for rec in records:
        buckets[key_fn(rec)].append(rec)
    entries = [
        LLMCallBreakdownEntry(
            key=key,
            call_count=len(group),
            prompt_tokens=sum(r.prompt_tokens for r in group),
            completion_tokens=sum(r.completion_tokens for r in group),
            total_tokens=sum(r.total_tokens for r in group),
        )
        for key, group in buckets.items()
    ]
    # Match Postgres ORDER BY total_tokens DESC so callers see the
    # same ranking regardless of backend.
    entries.sort(key=lambda e: e.total_tokens, reverse=True)
    return tuple(entries)


def _apply_window(
    stmt: Select[Any],
    since: datetime | None,
    until: datetime | None,
) -> Select[Any]:
    if since is not None:
        stmt = stmt.where(LLMCall.created_at >= since)
    if until is not None:
        stmt = stmt.where(LLMCall.created_at < until)
    return stmt


def _record_to_model(record: LLMCallRecord) -> LLMCall:
    return LLMCall(
        id=record.id,
        trace_id=record.trace_id,
        user_id=record.user_id,
        surface=record.surface,
        model=record.model,
        prompt_tokens=record.prompt_tokens,
        completion_tokens=record.completion_tokens,
        total_tokens=record.total_tokens,
        created_at=record.created_at,
    )
