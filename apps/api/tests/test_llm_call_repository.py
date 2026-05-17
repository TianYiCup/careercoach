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
from datetime import UTC, datetime, timedelta

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
