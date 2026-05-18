"""Unit tests for `InMemoryLLMCallRepository` (A-39 foundation).

These tests double as the conformance contract a Postgres-backed
implementation must also pass — both impls return the same dataclass
shapes and the same ORDER BY behavior (total_tokens desc).

A-39 ships in-memory only; the Postgres impl is exercised end-to-end
once A-40 wires the observability hook and A-42 hits the rollup
endpoint with a real DB.
"""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime, timedelta

from app.services.llm_calls import (
    InMemoryLLMCallRepository,
    LLMCallRecord,
    LLMCallTotals,
)

_NOW = datetime(2026, 5, 17, 12, 0, tzinfo=UTC)


def _record(
    *,
    user_id: str = "u_demo",
    surface: str = "sandbox",
    model: str = "deepseek-chat",
    prompt: int = 100,
    completion: int = 50,
    created_at: datetime | None = None,
    trace_id: str = "trace_aaa",
) -> LLMCallRecord:
    return LLMCallRecord(
        id=uuid.uuid4(),
        trace_id=trace_id,
        user_id=user_id,
        surface=surface,
        model=model,
        prompt_tokens=prompt,
        completion_tokens=completion,
        # Vendors report total_tokens = prompt + completion in the
        # simple case but can include reasoning/cached tokens — A-39
        # trusts the vendor's number rather than recomputing, and so
        # do these fixtures.
        total_tokens=prompt + completion,
        created_at=created_at or _NOW,
    )


# --- insert ---


async def test_insert_then_aggregate_by_user_returns_single_record() -> None:
    repo = InMemoryLLMCallRepository()
    await repo.insert(_record(prompt=120, completion=40))

    agg = await repo.aggregate_by_user("u_demo")

    assert agg.totals == LLMCallTotals(
        call_count=1, prompt_tokens=120, completion_tokens=40, total_tokens=160
    )
    assert agg.user_id == "u_demo"
    assert len(agg.by_model) == 1
    assert agg.by_model[0].key == "deepseek-chat"
    assert agg.by_model[0].total_tokens == 160


async def test_aggregate_by_user_empty_window_returns_zero_totals() -> None:
    """No records for the user → totals are zero, breakdowns are empty.
    The endpoint can render `total_tokens: 0` honestly rather than
    propagating a `None`-shaped response shape."""
    repo = InMemoryLLMCallRepository()

    agg = await repo.aggregate_by_user("u_demo")

    assert agg.totals == LLMCallTotals.zero()
    assert agg.by_model == ()
    assert agg.by_surface == ()


async def test_aggregate_by_user_filters_other_users() -> None:
    """Pinning the WHERE user_id clause — without it the rollup would
    leak other tenants' spend, which is the worst-case mistake here."""
    repo = InMemoryLLMCallRepository()
    await repo.insert(_record(user_id="u_alice", prompt=100, completion=50))
    await repo.insert(_record(user_id="u_bob", prompt=999, completion=999))

    agg = await repo.aggregate_by_user("u_alice")

    assert agg.totals.call_count == 1
    assert agg.totals.total_tokens == 150


# --- time window ---


async def test_aggregate_filters_records_older_than_since() -> None:
    repo = InMemoryLLMCallRepository()
    old = _NOW - timedelta(days=10)
    new = _NOW - timedelta(days=1)
    await repo.insert(_record(prompt=100, completion=100, created_at=old))
    await repo.insert(_record(prompt=200, completion=200, created_at=new))

    agg = await repo.aggregate_by_user("u_demo", since=_NOW - timedelta(days=7))

    # Only the 1-day-old record falls inside the 7-day window.
    assert agg.totals.call_count == 1
    assert agg.totals.total_tokens == 400


async def test_aggregate_filters_records_at_or_after_until() -> None:
    """The window is half-open `[since, until)` — matches Postgres's
    natural `created_at < until` semantics and avoids the ambiguity
    of inclusive upper bounds when callers chain consecutive windows
    (yesterday's `until` == today's `since`)."""
    repo = InMemoryLLMCallRepository()
    a = _NOW - timedelta(hours=2)
    b = _NOW + timedelta(hours=2)
    await repo.insert(_record(prompt=10, completion=10, created_at=a))
    await repo.insert(_record(prompt=20, completion=20, created_at=b))

    agg = await repo.aggregate_by_user("u_demo", until=_NOW)

    assert agg.totals.call_count == 1
    assert agg.totals.total_tokens == 20


async def test_aggregate_with_both_since_and_until_bounds() -> None:
    repo = InMemoryLLMCallRepository()
    inside = _NOW - timedelta(days=2)
    too_old = _NOW - timedelta(days=10)
    too_new = _NOW + timedelta(days=1)
    await repo.insert(_record(prompt=1, completion=1, created_at=too_old))
    await repo.insert(_record(prompt=50, completion=50, created_at=inside))
    await repo.insert(_record(prompt=999, completion=999, created_at=too_new))

    agg = await repo.aggregate_by_user(
        "u_demo",
        since=_NOW - timedelta(days=7),
        until=_NOW,
    )

    assert agg.totals.call_count == 1
    assert agg.totals.total_tokens == 100
    # Bounds echo back so a serializer doesn't have to re-derive them.
    assert agg.since == _NOW - timedelta(days=7)
    assert agg.until == _NOW


# --- breakdowns ---


async def test_by_model_breakdown_groups_by_model_string() -> None:
    repo = InMemoryLLMCallRepository()
    await repo.insert(_record(model="deepseek-chat", prompt=100, completion=50))
    await repo.insert(_record(model="deepseek-chat", prompt=200, completion=100))
    await repo.insert(_record(model="qwen-max", prompt=30, completion=20))

    agg = await repo.aggregate_by_user("u_demo")

    entries = {e.key: e for e in agg.by_model}
    assert entries["deepseek-chat"].call_count == 2
    assert entries["deepseek-chat"].total_tokens == 450
    assert entries["qwen-max"].call_count == 1
    assert entries["qwen-max"].total_tokens == 50


async def test_by_model_breakdown_is_sorted_by_total_tokens_desc() -> None:
    """The rollup endpoint's primary read is "what's burning my
    budget right now" — most-expensive first lets it render without
    re-sorting client-side."""
    repo = InMemoryLLMCallRepository()
    await repo.insert(_record(model="cheap", prompt=5, completion=5))
    await repo.insert(_record(model="expensive", prompt=1000, completion=1000))
    await repo.insert(_record(model="middle", prompt=100, completion=100))

    agg = await repo.aggregate_by_user("u_demo")

    keys = [e.key for e in agg.by_model]
    assert keys == ["expensive", "middle", "cheap"]


async def test_by_surface_breakdown_groups_by_surface_string() -> None:
    """Cross-surface visibility: "how much did sandbox vs review vs
    copilot cost this user?" Critical for the per-surface unit-economics
    question PRD §10 raises."""
    repo = InMemoryLLMCallRepository()
    await repo.insert(_record(surface="sandbox", prompt=100, completion=100))
    await repo.insert(_record(surface="sandbox", prompt=50, completion=50))
    await repo.insert(_record(surface="copilot", prompt=200, completion=200))
    await repo.insert(_record(surface="review", prompt=10, completion=10))

    agg = await repo.aggregate_by_user("u_demo")

    entries = {e.key: e for e in agg.by_surface}
    assert entries["sandbox"].call_count == 2
    assert entries["sandbox"].total_tokens == 300
    assert entries["copilot"].total_tokens == 400
    assert entries["review"].total_tokens == 20


async def test_by_surface_breakdown_is_sorted_by_total_tokens_desc() -> None:
    repo = InMemoryLLMCallRepository()
    await repo.insert(_record(surface="review", prompt=10, completion=10))
    await repo.insert(_record(surface="copilot", prompt=500, completion=500))
    await repo.insert(_record(surface="sandbox", prompt=100, completion=100))

    agg = await repo.aggregate_by_user("u_demo")

    keys = [e.key for e in agg.by_surface]
    assert keys == ["copilot", "sandbox", "review"]


async def test_breakdown_totals_match_overall_totals() -> None:
    """The sum across a breakdown must equal the headline totals.
    Without this invariant the rollup endpoint can render
    `total_tokens: 100` next to a by_model table summing to 95 — the
    kind of inconsistency that erodes trust in the dashboard fast."""
    repo = InMemoryLLMCallRepository()
    await repo.insert(_record(model="m1", surface="sandbox", prompt=10, completion=20))
    await repo.insert(_record(model="m2", surface="copilot", prompt=30, completion=40))
    await repo.insert(_record(model="m1", surface="review", prompt=5, completion=5))

    agg = await repo.aggregate_by_user("u_demo")

    by_model_total = sum(e.total_tokens for e in agg.by_model)
    by_surface_total = sum(e.total_tokens for e in agg.by_surface)
    assert agg.totals.total_tokens == by_model_total == by_surface_total == 110


async def test_aggregate_protocol_runtime_checkable() -> None:
    """Defensive: callers pulling the type via `Depends` should be
    able to runtime-isinstance the repo (e.g. for diagnostic
    logging). Pins the `@runtime_checkable` decoration so a future
    refactor that strips it surfaces here, not at the first prod
    callsite."""
    from app.services.llm_calls import LLMCallRepository

    repo = InMemoryLLMCallRepository()
    assert isinstance(repo, LLMCallRepository)


# --- daily aggregate (A-45) ---


def _utc_midnight(d: datetime) -> datetime:
    """Test helper — derive a clean midnight from any datetime."""
    return datetime(d.year, d.month, d.day, tzinfo=UTC)


async def test_daily_aggregate_returns_exactly_one_entry_per_day_in_window() -> None:
    """The repo zero-fills every UTC day in `[since.date(),
    until.date()]` — no gaps. Pinned so a regression that only
    returned active days would surface here, not in a dashboard
    chart with a broken x-axis."""
    repo = InMemoryLLMCallRepository()
    since = _utc_midnight(_NOW) - timedelta(days=6)  # 7 days ending today
    until = _NOW

    agg = await repo.aggregate_by_user_per_day("u_demo", since=since, until=until)

    assert len(agg.daily) == 7
    # Chronological order: oldest first.
    days = [e.day for e in agg.daily]
    assert days == sorted(days)
    assert days[0] == since.date()
    assert days[-1] == until.date()


async def test_daily_aggregate_empty_user_returns_all_zero_buckets() -> None:
    """No calls → N zero-totals entries. The chart line stays flat at
    zero rather than 404-ing or missing the days."""
    repo = InMemoryLLMCallRepository()
    since = _utc_midnight(_NOW) - timedelta(days=2)
    until = _NOW

    agg = await repo.aggregate_by_user_per_day("u_demo", since=since, until=until)

    assert len(agg.daily) == 3
    assert all(e.totals == LLMCallTotals.zero() for e in agg.daily)
    assert agg.totals == LLMCallTotals.zero()


async def test_daily_aggregate_buckets_records_by_utc_date() -> None:
    repo = InMemoryLLMCallRepository()
    day_a = datetime(2026, 5, 15, 8, 0, tzinfo=UTC)
    day_b = datetime(2026, 5, 17, 12, 0, tzinfo=UTC)
    await repo.insert(_record(prompt=100, completion=50, created_at=day_a))
    await repo.insert(_record(prompt=200, completion=100, created_at=day_a))
    await repo.insert(_record(prompt=30, completion=20, created_at=day_b))

    agg = await repo.aggregate_by_user_per_day(
        "u_demo",
        since=datetime(2026, 5, 15, 0, 0, tzinfo=UTC),
        until=datetime(2026, 5, 18, 0, 0, tzinfo=UTC),
    )

    by_date = {e.day: e.totals for e in agg.daily}
    # Day A: 2 calls, 450 tokens; Day B (5-17): 1 call, 50 tokens;
    # Day in-between (5-16): zero-filled.
    assert by_date[date(2026, 5, 15)].call_count == 2
    assert by_date[date(2026, 5, 15)].total_tokens == 450
    assert by_date[date(2026, 5, 16)] == LLMCallTotals.zero()
    assert by_date[date(2026, 5, 17)].call_count == 1
    assert by_date[date(2026, 5, 17)].total_tokens == 50


async def test_daily_aggregate_totals_equal_sum_of_daily_buckets() -> None:
    """Headline invariant: the rollup `totals` MUST equal the sum
    of the per-day totals. Dashboards lose trust the moment the
    headline number disagrees with the chart sum."""
    repo = InMemoryLLMCallRepository()
    base = datetime(2026, 5, 15, 12, 0, tzinfo=UTC)
    await repo.insert(_record(prompt=10, completion=5, created_at=base))
    await repo.insert(_record(prompt=20, completion=10, created_at=base + timedelta(days=1)))
    await repo.insert(_record(prompt=30, completion=15, created_at=base + timedelta(days=2)))

    agg = await repo.aggregate_by_user_per_day(
        "u_demo",
        since=datetime(2026, 5, 15, 0, 0, tzinfo=UTC),
        until=datetime(2026, 5, 18, 0, 0, tzinfo=UTC),
    )

    daily_sum = sum(e.totals.total_tokens for e in agg.daily)
    assert agg.totals.total_tokens == daily_sum == 90
    daily_call_sum = sum(e.totals.call_count for e in agg.daily)
    assert agg.totals.call_count == daily_call_sum == 3


async def test_daily_aggregate_filters_other_users() -> None:
    """Cross-tenant isolation at the daily path — same pin as
    `aggregate_by_user` but for the time-series query."""
    repo = InMemoryLLMCallRepository()
    when = datetime(2026, 5, 17, 12, 0, tzinfo=UTC)
    await repo.insert(_record(user_id="u_alice", prompt=100, completion=50, created_at=when))
    await repo.insert(_record(user_id="u_bob", prompt=999, completion=999, created_at=when))

    agg = await repo.aggregate_by_user_per_day(
        "u_alice",
        since=datetime(2026, 5, 17, 0, 0, tzinfo=UTC),
        until=datetime(2026, 5, 18, 0, 0, tzinfo=UTC),
    )

    assert agg.totals.total_tokens == 150
    assert agg.daily[0].totals.total_tokens == 150


async def test_daily_aggregate_excludes_records_outside_window() -> None:
    """Half-open `[since, until)` matches `aggregate_by_user` semantics."""
    repo = InMemoryLLMCallRepository()
    before = datetime(2026, 5, 10, 12, 0, tzinfo=UTC)
    inside = datetime(2026, 5, 16, 12, 0, tzinfo=UTC)
    on_until = datetime(2026, 5, 18, 0, 0, tzinfo=UTC)  # exactly the until boundary
    await repo.insert(_record(prompt=999, completion=999, created_at=before))
    await repo.insert(_record(prompt=10, completion=10, created_at=inside))
    await repo.insert(_record(prompt=888, completion=888, created_at=on_until))

    agg = await repo.aggregate_by_user_per_day(
        "u_demo",
        since=datetime(2026, 5, 15, 0, 0, tzinfo=UTC),
        until=datetime(2026, 5, 18, 0, 0, tzinfo=UTC),
    )

    # Only the inside record contributes.
    assert agg.totals.call_count == 1
    assert agg.totals.total_tokens == 20


async def test_daily_aggregate_single_day_window() -> None:
    """`days=1` is the smallest legal window. Pin that the math
    doesn't off-by-one — should return 1 bucket covering today."""
    repo = InMemoryLLMCallRepository()
    since = _utc_midnight(_NOW)
    until = _NOW

    agg = await repo.aggregate_by_user_per_day("u_demo", since=since, until=until)

    assert len(agg.daily) == 1
    assert agg.daily[0].day == until.date()
