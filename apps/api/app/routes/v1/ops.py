"""Ops-only `/v1/ops/*` HTTP surface — staff-side cost + diagnostics.

Endpoints in this namespace (all gated by `require_ops_token`, A-41):

  * GET /v1/ops/token-cost            (A-42) — per-user LLM spend rollup
  * GET /v1/ops/moderation-events     (A-43) — moderation decision tail
  * GET /v1/ops/moderation-stats      (A-44) — moderation rate rollup
  * GET /v1/ops/token-cost-daily      (A-45) — token spend per UTC day
  * GET /v1/ops/llm-calls             (A-46) — LLM call tail (drill-down)

Every endpoint here is gated by `require_ops_token` (A-41), which
checks the `X-Ops-Token` header. The dep fails closed when
`OPS_API_TOKEN` is unset — a forgotten env var must never silently
open the rollup endpoint to the public.

Reads only. Ops routes that mutate state can land later in their own
files under the same prefix.
"""

from __future__ import annotations

from datetime import UTC, datetime, time, timedelta

from fastapi import APIRouter, Depends, Query

from app.schemas.moderation import ModerationVerdict
from app.schemas.ops import (
    MAX_LLM_CALLS_LIMIT,
    MAX_MODERATION_EVENTS_LIMIT,
    MAX_TOKEN_COST_DAILY_DAYS,
    LLMCallEntry,
    LLMCallsResponse,
    LLMCallSurface,
    ModerationEventEntry,
    ModerationEventsResponse,
    ModerationStatsBreakdownEntry,
    ModerationStatsResponse,
    ModerationStatsTotals,
    TokenCostBreakdownEntry,
    TokenCostDailyEntry,
    TokenCostDailyResponse,
    TokenCostResponse,
    TokenCostTotals,
    TokenCostWindow,
)
from app.services.llm_calls import (
    LLMCallAggregate,
    LLMCallBreakdownEntry,
    LLMCallDailyAggregate,
    LLMCallDailyEntry,
    LLMCallRecord,
    LLMCallRepository,
    get_llm_call_repository,
)
from app.services.moderation_events import (
    ModerationEventAggregate,
    ModerationEventRecord,
    ModerationEventRepository,
    get_moderation_event_repository,
)
from app.services.moderation_events import (
    ModerationStatsBreakdownEntry as RepoStatsBreakdownEntry,
)
from app.services.ops import require_ops_token

router = APIRouter(prefix="/ops", tags=["ops"])


def _get_repo() -> LLMCallRepository:
    """Tiny wrapper so FastAPI's `Depends` sees a callable rather
    than a bare `lru_cache`-d function — keeps `dependency_overrides`
    in tests symmetrical with the user-side route deps."""
    return get_llm_call_repository()


_WINDOW_DURATIONS: dict[TokenCostWindow, timedelta | None] = {
    "1d": timedelta(days=1),
    "7d": timedelta(days=7),
    "30d": timedelta(days=30),
    # `all` passes no bounds to the repo — useful for "what has this
    # user spent across the full retention window we have on file".
    "all": None,
}


@router.get(
    "/token-cost",
    response_model=TokenCostResponse,
    summary="Per-user LLM token spend rollup over a fixed time window",
    description=(
        "Aggregates `llm_calls` rows for one user across the requested "
        "window. Returns headline totals plus by-model and by-surface "
        "breakdowns, each sorted by `total_tokens` descending. "
        "`window=all` skips time bounds; otherwise the response's "
        "`since`/`until` echo the resolved bounds for cacheability."
    ),
    responses={
        401: {"description": "missing or wrong X-Ops-Token header"},
        503: {"description": "ops endpoints disabled (OPS_API_TOKEN unset)"},
    },
    dependencies=[Depends(require_ops_token)],
)
async def get_token_cost(
    user_id: str = Query(
        ...,
        min_length=1,
        description=(
            "The user whose spend to roll up. Pass the literal "
            "`system` for non-user-attributed calls (cron, background)."
        ),
        examples=["u_018f3a8b1c2d7e3a"],
    ),
    window: TokenCostWindow = Query(
        default="7d",
        description=(
            "Time window for the rollup. `1d` / `7d` / `30d` are "
            "rolling windows ending at request time; `all` is "
            "unbounded."
        ),
    ),
    repo: LLMCallRepository = Depends(_get_repo),
) -> TokenCostResponse:
    """Resolve the window to since/until, call the repo, repackage
    the dataclass result as the wire schema, stamp `generated_at`."""
    now = datetime.now(UTC)
    duration = _WINDOW_DURATIONS[window]
    since = now - duration if duration is not None else None
    until = now if duration is not None else None

    aggregate = await repo.aggregate_by_user(user_id, since=since, until=until)

    return _build_response(aggregate=aggregate, window=window, generated_at=now)


def _build_response(
    *,
    aggregate: LLMCallAggregate,
    window: TokenCostWindow,
    generated_at: datetime,
) -> TokenCostResponse:
    """Repackage the repo's dataclass result as the wire schema.

    Pulled into a helper so the route stays a thin pass-through and
    a future caller (e.g. a CLI that bypasses the route) can reuse
    the same transformation without re-implementing it.
    """
    return TokenCostResponse(
        user_id=aggregate.user_id,
        window=window,
        since=aggregate.since,
        until=aggregate.until,
        totals=TokenCostTotals(
            call_count=aggregate.totals.call_count,
            prompt_tokens=aggregate.totals.prompt_tokens,
            completion_tokens=aggregate.totals.completion_tokens,
            total_tokens=aggregate.totals.total_tokens,
        ),
        by_model=[_breakdown_entry(e) for e in aggregate.by_model],
        by_surface=[_breakdown_entry(e) for e in aggregate.by_surface],
        generated_at=generated_at,
    )


def _breakdown_entry(entry: LLMCallBreakdownEntry) -> TokenCostBreakdownEntry:
    return TokenCostBreakdownEntry(
        key=entry.key,
        call_count=entry.call_count,
        prompt_tokens=entry.prompt_tokens,
        completion_tokens=entry.completion_tokens,
        total_tokens=entry.total_tokens,
    )


# --- A-43 moderation event tail ---


def _get_events_repo() -> ModerationEventRepository:
    """Tiny wrapper so FastAPI's `Depends` sees a callable rather
    than a bare `lru_cache`-d function — same pattern as `_get_repo`
    above. Tests override via `app.dependency_overrides[_get_events_repo]`."""
    return get_moderation_event_repository()


@router.get(
    "/moderation-events",
    response_model=ModerationEventsResponse,
    summary="Recent moderation decisions (audit tail)",
    description=(
        "Returns the most recent `moderation_events` rows, newest "
        "first. Filters compose (`user_id`, `verdict`, `since`, "
        "`until`); omit them for an unfiltered tail. Raw content is "
        "never returned — only the SHA-256 hash and length. The "
        f"per-page cap is {MAX_MODERATION_EVENTS_LIMIT}."
    ),
    responses={
        401: {"description": "missing or wrong X-Ops-Token header"},
        503: {"description": "ops endpoints disabled (OPS_API_TOKEN unset)"},
    },
    dependencies=[Depends(require_ops_token)],
)
async def list_moderation_events(
    user_id: str | None = Query(
        default=None,
        min_length=1,
        description="Filter to a single user. Omit for tenant-wide tail.",
        examples=["u_018f3a8b1c2d7e3a"],
    ),
    verdict: ModerationVerdict | None = Query(
        default=None,
        description=(
            "Filter to one verdict (`allow` / `warn` / `redirect` / "
            "`block`). Omit to include every verdict."
        ),
    ),
    since: datetime | None = Query(
        default=None,
        description=(
            "Inclusive lower bound on `created_at`. ISO-8601, "
            "timezone-aware. Omit for no lower bound."
        ),
    ),
    until: datetime | None = Query(
        default=None,
        description=(
            "Exclusive upper bound on `created_at` (half-open "
            "`[since, until)` matches the rollup window semantics). "
            "Omit for no upper bound."
        ),
    ),
    limit: int = Query(
        default=50,
        ge=1,
        le=MAX_MODERATION_EVENTS_LIMIT,
        description=(
            "Per-page cap on rows returned. Default 50 (typical ops "
            f"tail render); hard max {MAX_MODERATION_EVENTS_LIMIT}."
        ),
    ),
    repo: ModerationEventRepository = Depends(_get_events_repo),
) -> ModerationEventsResponse:
    """Query the repo, repackage each record as the wire schema,
    echo back the filters and stamp `generated_at`."""
    now = datetime.now(UTC)
    records = await repo.list_events(
        since=since,
        until=until,
        user_id=user_id,
        verdict=verdict,
        limit=limit,
    )
    events = [_event_entry(r) for r in records]
    return ModerationEventsResponse(
        since=since,
        until=until,
        user_id=user_id,
        verdict=verdict,
        limit=limit,
        count=len(events),
        events=events,
        generated_at=now,
    )


def _event_entry(record: ModerationEventRecord) -> ModerationEventEntry:
    """Repackage a repo record as the wire schema.

    `id` is serialized as the UUID's plain hex form (the JSON spec
    doesn't have a UUID type — keeping it a string everywhere avoids
    silent round-trip drift if a future client deserializes back).
    `categories` is widened to `list[str]` for Pydantic's literal
    validation to accept it — the values themselves are already
    constrained to the ModerationCategory enum by the moderation
    pipeline (audit log only ever stores values the backend produced).
    """
    return ModerationEventEntry(
        id=str(record.id),
        user_id=record.user_id,
        session_id=record.session_id,
        content_hash=record.content_hash,
        content_length=record.content_length,
        context=record.context,
        verdict=record.verdict,
        categories=list(record.categories),
        score=record.score,
        backend=record.backend,
        trace_id=record.trace_id,
        created_at=record.created_at,
    )


# --- A-44 moderation rate stats ---


@router.get(
    "/moderation-stats",
    response_model=ModerationStatsResponse,
    summary="Rate rollup over moderation decisions",
    description=(
        "Aggregates `moderation_events` over a window. Returns "
        "totals plus by-verdict (always 4 entries in canonical "
        "order), by-context, by-category and by-backend breakdowns. "
        "`by_category` counts each row once per category it carries — "
        "the reading is 'how often did category X fire', not 'how "
        "many single-category rows'."
    ),
    responses={
        401: {"description": "missing or wrong X-Ops-Token header"},
        503: {"description": "ops endpoints disabled (OPS_API_TOKEN unset)"},
    },
    dependencies=[Depends(require_ops_token)],
)
async def get_moderation_stats(
    user_id: str | None = Query(
        default=None,
        min_length=1,
        description="Filter to a single user. Omit for tenant-wide rollup.",
        examples=["u_018f3a8b1c2d7e3a"],
    ),
    since: datetime | None = Query(
        default=None,
        description="Inclusive lower bound on `created_at`. ISO-8601, timezone-aware.",
    ),
    until: datetime | None = Query(
        default=None,
        description=(
            "Exclusive upper bound on `created_at` (half-open "
            "`[since, until)` matches /moderation-events + /token-cost)."
        ),
    ),
    repo: ModerationEventRepository = Depends(_get_events_repo),
) -> ModerationStatsResponse:
    """Resolve filters, call the repo, repackage the aggregate as
    the wire schema, stamp `generated_at`."""
    now = datetime.now(UTC)
    aggregate = await repo.aggregate(since=since, until=until, user_id=user_id)
    return _build_stats_response(aggregate=aggregate, generated_at=now)


def _build_stats_response(
    *,
    aggregate: ModerationEventAggregate,
    generated_at: datetime,
) -> ModerationStatsResponse:
    """Repackage the repo's aggregate dataclass as the wire schema.

    Pulled into a helper so the route stays a thin pass-through and
    a future caller (CLI, internal dashboard) can reuse the same
    transformation without re-implementing it.
    """
    return ModerationStatsResponse(
        since=aggregate.since,
        until=aggregate.until,
        user_id=aggregate.user_id,
        totals=ModerationStatsTotals(
            event_count=aggregate.totals.event_count,
            allow_count=aggregate.totals.allow_count,
            warn_count=aggregate.totals.warn_count,
            redirect_count=aggregate.totals.redirect_count,
            block_count=aggregate.totals.block_count,
        ),
        by_verdict=[_stats_entry(e) for e in aggregate.by_verdict],
        by_context=[_stats_entry(e) for e in aggregate.by_context],
        by_category=[_stats_entry(e) for e in aggregate.by_category],
        by_backend=[_stats_entry(e) for e in aggregate.by_backend],
        generated_at=generated_at,
    )


def _stats_entry(entry: RepoStatsBreakdownEntry) -> ModerationStatsBreakdownEntry:
    return ModerationStatsBreakdownEntry(key=entry.key, count=entry.count)


# --- A-45 token-cost daily time series ---


@router.get(
    "/token-cost-daily",
    response_model=TokenCostDailyResponse,
    summary="Per-user token spend bucketed by UTC day",
    description=(
        "Returns N consecutive UTC-day buckets ending today (the "
        "today bucket is partial, with `until` set to request time). "
        "Days with no calls appear with zero totals so a chart line "
        "renders without gaps. The headline `totals` equals the sum "
        f"across the daily buckets. Max `days` is {MAX_TOKEN_COST_DAILY_DAYS}."
    ),
    responses={
        401: {"description": "missing or wrong X-Ops-Token header"},
        503: {"description": "ops endpoints disabled (OPS_API_TOKEN unset)"},
    },
    dependencies=[Depends(require_ops_token)],
)
async def get_token_cost_daily(
    user_id: str = Query(
        ...,
        min_length=1,
        description="The user whose daily spend to roll up.",
        examples=["u_018f3a8b1c2d7e3a"],
    ),
    days: int = Query(
        default=7,
        ge=1,
        le=MAX_TOKEN_COST_DAILY_DAYS,
        description=(
            "Number of UTC-day buckets to return, ending today. "
            f"1 = today only; default 7 = week view; max {MAX_TOKEN_COST_DAILY_DAYS}."
        ),
    ),
    repo: LLMCallRepository = Depends(_get_repo),
) -> TokenCostDailyResponse:
    """Resolve the window to UTC-midnight-aligned `since` + `until=now`,
    call the repo, repackage the per-day aggregate as the wire schema,
    stamp `generated_at`."""
    now = datetime.now(UTC)
    # Align `since` to UTC midnight `days-1` days back so the window
    # covers exactly `days` buckets. The today bucket is partial — that
    # matches what an ops dashboard expects ("today so far").
    start_date = now.date() - timedelta(days=days - 1)
    since = datetime.combine(start_date, time.min, tzinfo=UTC)
    until = now

    aggregate = await repo.aggregate_by_user_per_day(user_id, since=since, until=until)
    return _build_daily_response(aggregate=aggregate, days=days, generated_at=now)


def _build_daily_response(
    *,
    aggregate: LLMCallDailyAggregate,
    days: int,
    generated_at: datetime,
) -> TokenCostDailyResponse:
    """Repackage the repo's daily aggregate as the wire schema.

    `days` is the requested-and-resolved bucket count from the route.
    It matches `len(aggregate.daily)` by construction (the repo
    zero-fills missing days across `[since.date(), until.date()]`),
    so we surface the caller's input verbatim in the response.
    """
    return TokenCostDailyResponse(
        user_id=aggregate.user_id,
        days=days,
        since=aggregate.since,
        until=aggregate.until,
        totals=TokenCostTotals(
            call_count=aggregate.totals.call_count,
            prompt_tokens=aggregate.totals.prompt_tokens,
            completion_tokens=aggregate.totals.completion_tokens,
            total_tokens=aggregate.totals.total_tokens,
        ),
        daily=[_daily_entry(e) for e in aggregate.daily],
        generated_at=generated_at,
    )


def _daily_entry(entry: LLMCallDailyEntry) -> TokenCostDailyEntry:
    return TokenCostDailyEntry(
        day=entry.day,
        totals=TokenCostTotals(
            call_count=entry.totals.call_count,
            prompt_tokens=entry.totals.prompt_tokens,
            completion_tokens=entry.totals.completion_tokens,
            total_tokens=entry.totals.total_tokens,
        ),
    )


# --- A-46 LLM call tail ---


@router.get(
    "/llm-calls",
    response_model=LLMCallsResponse,
    summary="Recent LLM calls (drill-down tail)",
    description=(
        "Returns the most recent `llm_calls` rows, newest first. "
        "The drill-down companion to `/token-cost` and `/token-cost-daily` "
        "— a chart spike in those endpoints typically leads here to "
        "see which specific traces drove it. Filters compose "
        "(`user_id`, `surface`, `model`, `since`, `until`); omit them "
        f"for an unfiltered tail. Per-page cap is {MAX_LLM_CALLS_LIMIT}."
    ),
    responses={
        401: {"description": "missing or wrong X-Ops-Token header"},
        503: {"description": "ops endpoints disabled (OPS_API_TOKEN unset)"},
    },
    dependencies=[Depends(require_ops_token)],
)
async def list_llm_calls(
    user_id: str | None = Query(
        default=None,
        min_length=1,
        description="Filter to a single user. Omit for tenant-wide tail.",
        examples=["u_018f3a8b1c2d7e3a"],
    ),
    surface: LLMCallSurface | None = Query(
        default=None,
        description=(
            "Filter to one surface (`sandbox` / `review` / `copilot` / "
            "`agent`). Omit to include every surface."
        ),
    ),
    model: str | None = Query(
        default=None,
        min_length=1,
        description=(
            "Filter to one vendor model id (e.g. `deepseek-chat`, "
            "`qwen-max`). String match against the recorded value."
        ),
    ),
    since: datetime | None = Query(
        default=None,
        description="Inclusive lower bound on `created_at`. ISO-8601, timezone-aware.",
    ),
    until: datetime | None = Query(
        default=None,
        description=(
            "Exclusive upper bound on `created_at` (half-open "
            "`[since, until)` matches the rollup window semantics)."
        ),
    ),
    limit: int = Query(
        default=50,
        ge=1,
        le=MAX_LLM_CALLS_LIMIT,
        description=(
            "Per-page cap on rows returned. Default 50 (typical ops "
            f"tail render); hard max {MAX_LLM_CALLS_LIMIT}."
        ),
    ),
    repo: LLMCallRepository = Depends(_get_repo),
) -> LLMCallsResponse:
    """Query the repo, repackage each record as the wire schema,
    echo back filters, stamp `generated_at`."""
    now = datetime.now(UTC)
    records = await repo.list_calls(
        since=since,
        until=until,
        user_id=user_id,
        surface=surface,
        model=model,
        limit=limit,
    )
    calls = [_call_entry(r) for r in records]
    return LLMCallsResponse(
        since=since,
        until=until,
        user_id=user_id,
        surface=surface,
        model=model,
        limit=limit,
        count=len(calls),
        calls=calls,
        generated_at=now,
    )


def _call_entry(record: LLMCallRecord) -> LLMCallEntry:
    """Repackage a repo record as the wire schema.

    `id` serializes as the UUID's plain hex form (the JSON spec
    doesn't have a UUID type — keeping it a string everywhere
    avoids round-trip drift). `surface` is widened to `str` at the
    dataclass level but constrained back to the Literal at the
    schema layer; values landed in the table by the
    `record_generation` callsites and are already from the enum.
    """
    return LLMCallEntry(
        id=str(record.id),
        trace_id=record.trace_id,
        user_id=record.user_id,
        surface=record.surface,
        model=record.model,
        prompt_tokens=record.prompt_tokens,
        completion_tokens=record.completion_tokens,
        total_tokens=record.total_tokens,
        created_at=record.created_at,
    )


__all__ = ["router"]
