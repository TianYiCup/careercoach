"""Unit tests for `InMemoryModerationEventRepository` (A-43 foundation).

These tests double as the conformance contract a Postgres-backed
implementation must also pass — both impls return the same dataclass
shape, the same most-recent-first ORDER BY, and the same filter
composition rules.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

from app.services.moderation_events import (
    InMemoryModerationEventRepository,
    ModerationEventRecord,
    ModerationEventRepository,
)

_NOW = datetime(2026, 5, 17, 12, 0, tzinfo=UTC)
_HASH = "a" * 64  # SHA-256 hex is always 64 chars; any valid hex string works for tests


def _record(
    *,
    user_id: str = "u_demo",
    session_id: str | None = "s_001",
    context: str = "user_input",
    verdict: str = "allow",
    categories: tuple[str, ...] = (),
    score: float = 0.1,
    backend: str = "local_dict",
    trace_id: str = "trace_aaa",
    content_length: int = 32,
    created_at: datetime | None = None,
) -> ModerationEventRecord:
    return ModerationEventRecord(
        id=uuid.uuid4(),
        user_id=user_id,
        session_id=session_id,
        content_hash=_HASH,
        content_length=content_length,
        context=context,
        verdict=verdict,
        categories=categories,
        score=score,
        backend=backend,
        trace_id=trace_id,
        created_at=created_at or _NOW,
    )


# --- insert + basic read ---


async def test_insert_then_list_returns_single_record() -> None:
    repo = InMemoryModerationEventRepository()
    rec = _record()
    await repo.insert(rec)

    events = await repo.list_events()

    assert len(events) == 1
    assert events[0].id == rec.id
    assert events[0].user_id == "u_demo"
    assert events[0].verdict == "allow"


async def test_list_empty_repo_returns_empty_list() -> None:
    """No rows → empty list, not None — pins that the endpoint can
    safely json-serialize without a special-case for the empty repo."""
    repo = InMemoryModerationEventRepository()

    events = await repo.list_events()

    assert events == []


# --- ordering: newest first ---


async def test_list_events_orders_newest_first() -> None:
    """Most recent on top — that's the ops tail contract. Without
    this, a future refactor that swaps the sort direction would
    silently flip the ordering of every dashboard."""
    repo = InMemoryModerationEventRepository()
    old = _NOW - timedelta(hours=5)
    mid = _NOW - timedelta(hours=2)
    new = _NOW - timedelta(minutes=10)
    await repo.insert(_record(trace_id="t_old", created_at=old))
    await repo.insert(_record(trace_id="t_mid", created_at=mid))
    await repo.insert(_record(trace_id="t_new", created_at=new))

    events = await repo.list_events()

    assert [e.trace_id for e in events] == ["t_new", "t_mid", "t_old"]


# --- limit ---


async def test_list_events_respects_limit() -> None:
    repo = InMemoryModerationEventRepository()
    for i in range(10):
        await repo.insert(_record(trace_id=f"t_{i}", created_at=_NOW - timedelta(minutes=i)))

    events = await repo.list_events(limit=3)

    # Limit applies AFTER sort — we get the 3 newest, not 3 random.
    assert len(events) == 3
    assert [e.trace_id for e in events] == ["t_0", "t_1", "t_2"]


async def test_list_events_default_limit_is_50() -> None:
    """Default cap doc-pinned to 50 — the route layer surfaces it
    as the Query default, but the repo also enforces it so an
    ad-hoc CLI caller can't accidentally page 100k rows by
    skipping the kwarg."""
    repo = InMemoryModerationEventRepository()
    for i in range(120):
        await repo.insert(_record(trace_id=f"t_{i}", created_at=_NOW - timedelta(seconds=i)))

    events = await repo.list_events()

    assert len(events) == 50


# --- time bounds (half-open [since, until)) ---


async def test_list_events_filters_older_than_since() -> None:
    repo = InMemoryModerationEventRepository()
    too_old = _NOW - timedelta(days=10)
    fresh = _NOW - timedelta(hours=1)
    await repo.insert(_record(trace_id="t_old", created_at=too_old))
    await repo.insert(_record(trace_id="t_fresh", created_at=fresh))

    events = await repo.list_events(since=_NOW - timedelta(days=1))

    assert [e.trace_id for e in events] == ["t_fresh"]


async def test_list_events_filters_at_or_after_until() -> None:
    """Half-open `[since, until)` — exactly matches the cost-rollup
    window semantics so consecutive ops pages can be chained without
    overlap or gap."""
    repo = InMemoryModerationEventRepository()
    inside = _NOW - timedelta(hours=2)
    on_boundary = _NOW
    after = _NOW + timedelta(hours=2)
    await repo.insert(_record(trace_id="t_inside", created_at=inside))
    await repo.insert(_record(trace_id="t_boundary", created_at=on_boundary))
    await repo.insert(_record(trace_id="t_after", created_at=after))

    events = await repo.list_events(until=_NOW)

    # `until=_NOW` excludes records with created_at == _NOW.
    assert [e.trace_id for e in events] == ["t_inside"]


async def test_list_events_with_both_since_and_until_bounds() -> None:
    repo = InMemoryModerationEventRepository()
    too_old = _NOW - timedelta(days=10)
    inside = _NOW - timedelta(days=2)
    too_new = _NOW + timedelta(days=1)
    await repo.insert(_record(trace_id="t_too_old", created_at=too_old))
    await repo.insert(_record(trace_id="t_inside", created_at=inside))
    await repo.insert(_record(trace_id="t_too_new", created_at=too_new))

    events = await repo.list_events(
        since=_NOW - timedelta(days=7),
        until=_NOW,
    )

    assert [e.trace_id for e in events] == ["t_inside"]


# --- user_id / verdict filters ---


async def test_list_events_filters_by_user_id() -> None:
    """Cross-tenant isolation — the repo MUST honor `user_id`, even
    when other filters are absent. Without this an ops query would
    leak between tenants by default, which is the worst-case
    drill-down mistake."""
    repo = InMemoryModerationEventRepository()
    await repo.insert(_record(user_id="u_alice"))
    await repo.insert(_record(user_id="u_bob"))
    await repo.insert(_record(user_id="u_alice"))

    events = await repo.list_events(user_id="u_alice")

    assert len(events) == 2
    assert all(e.user_id == "u_alice" for e in events)


async def test_list_events_filters_by_verdict() -> None:
    repo = InMemoryModerationEventRepository()
    await repo.insert(_record(verdict="allow", trace_id="t_allow"))
    await repo.insert(_record(verdict="block", trace_id="t_block"))
    await repo.insert(_record(verdict="redirect", trace_id="t_redirect"))

    events = await repo.list_events(verdict="block")

    assert [e.trace_id for e in events] == ["t_block"]


async def test_list_events_filters_compose() -> None:
    """When multiple filters are set they're AND-ed — pinned so a
    future refactor that flips to OR-semantics surfaces here."""
    repo = InMemoryModerationEventRepository()
    await repo.insert(_record(user_id="u_alice", verdict="block", trace_id="t_match"))
    await repo.insert(_record(user_id="u_alice", verdict="allow", trace_id="t_user_only"))
    await repo.insert(_record(user_id="u_bob", verdict="block", trace_id="t_verdict_only"))

    events = await repo.list_events(user_id="u_alice", verdict="block")

    assert [e.trace_id for e in events] == ["t_match"]


# --- content opacity (the privacy invariant) ---


async def test_list_events_never_exposes_raw_content() -> None:
    """The record dataclass intentionally has no `content` field —
    only `content_hash` + `content_length`. Pinned at the type level
    so a future refactor adding raw content would have to delete
    this test, making the privacy regression visible in review."""
    repo = InMemoryModerationEventRepository()
    await repo.insert(_record())

    events = await repo.list_events()

    field_names = set(events[0].__dataclass_fields__)
    assert "content" not in field_names
    assert "content_hash" in field_names
    assert "content_length" in field_names


# --- Protocol conformance ---


async def test_repository_protocol_runtime_checkable() -> None:
    """Pin `@runtime_checkable` so a future refactor that strips it
    surfaces here, not at the first prod callsite that wants to
    `isinstance(repo, ModerationEventRepository)` for diagnostics."""
    repo = InMemoryModerationEventRepository()
    assert isinstance(repo, ModerationEventRepository)


# --- aggregate (A-44) ---


async def test_aggregate_empty_repo_returns_zero_totals() -> None:
    """Empty window → all counters zero, breakdowns empty (except
    by_verdict which always lists 4). Pins that the route can render
    a "no events yet" dashboard honestly without a special case."""
    repo = InMemoryModerationEventRepository()

    agg = await repo.aggregate()

    assert agg.totals.event_count == 0
    assert agg.totals.allow_count == 0
    assert agg.totals.block_count == 0
    assert len(agg.by_verdict) == 4  # always 4, even when zero
    assert all(e.count == 0 for e in agg.by_verdict)
    assert agg.by_context == ()
    assert agg.by_category == ()
    assert agg.by_backend == ()


async def test_aggregate_counts_verdicts_correctly() -> None:
    repo = InMemoryModerationEventRepository()
    await repo.insert(_record(verdict="allow"))
    await repo.insert(_record(verdict="allow"))
    await repo.insert(_record(verdict="allow"))
    await repo.insert(_record(verdict="warn"))
    await repo.insert(_record(verdict="block"))

    agg = await repo.aggregate()

    assert agg.totals.event_count == 5
    assert agg.totals.allow_count == 3
    assert agg.totals.warn_count == 1
    assert agg.totals.redirect_count == 0
    assert agg.totals.block_count == 1


async def test_aggregate_by_verdict_always_includes_all_four_in_canonical_order() -> None:
    """Pin the contract: by_verdict is ALWAYS 4 entries in the order
    (allow, warn, redirect, block) regardless of which verdicts the
    window contains. Gives dashboards a stable column layout — a
    zero-allow window still renders the allow column."""
    repo = InMemoryModerationEventRepository()
    await repo.insert(_record(verdict="block"))
    # Only block events — but by_verdict must still surface all 4 keys
    # so the dashboard stays self-consistent.

    agg = await repo.aggregate()

    keys = [e.key for e in agg.by_verdict]
    assert keys == ["allow", "warn", "redirect", "block"]
    counts = {e.key: e.count for e in agg.by_verdict}
    assert counts == {"allow": 0, "warn": 0, "redirect": 0, "block": 1}


async def test_aggregate_by_context_counts_each_context() -> None:
    repo = InMemoryModerationEventRepository()
    await repo.insert(_record(context="user_input"))
    await repo.insert(_record(context="user_input"))
    await repo.insert(_record(context="ai_output"))
    await repo.insert(_record(context="scenario_custom"))

    agg = await repo.aggregate()

    entries = {e.key: e.count for e in agg.by_context}
    assert entries == {"user_input": 2, "ai_output": 1, "scenario_custom": 1}


async def test_aggregate_by_context_sorted_by_count_desc() -> None:
    """Most-frequent first — the read most ops queries are looking
    for is "what's on top"."""
    repo = InMemoryModerationEventRepository()
    await repo.insert(_record(context="ai_output"))
    await repo.insert(_record(context="ai_output"))
    await repo.insert(_record(context="ai_output"))
    await repo.insert(_record(context="user_input"))

    agg = await repo.aggregate()

    keys = [e.key for e in agg.by_context]
    assert keys == ["ai_output", "user_input"]


async def test_aggregate_by_category_counts_each_tag_per_row() -> None:
    """Critical invariant: a row tagged with (self_harm, violence)
    contributes ONCE to each bucket. The reading is "how often did
    category X fire", not "how many rows had exactly one category".
    Without this, a dashboard showing "self_harm: 10" could hide
    that 8 of those were also violence — and ops needs to see both."""
    repo = InMemoryModerationEventRepository()
    await repo.insert(_record(categories=("self_harm", "violence")))
    await repo.insert(_record(categories=("self_harm",)))
    await repo.insert(_record(categories=("violence",)))
    await repo.insert(_record(categories=()))  # allow row — no categories

    agg = await repo.aggregate()

    counts = {e.key: e.count for e in agg.by_category}
    assert counts == {"self_harm": 2, "violence": 2}
    # And empty-category rows don't introduce a phantom "" bucket.
    assert "" not in counts


async def test_aggregate_by_backend_counts_each_backend() -> None:
    """Lets ops estimate how often the cloud-mod cascade fell through
    to the local dict (PRD §7.8.5 cost-control)."""
    repo = InMemoryModerationEventRepository()
    await repo.insert(_record(backend="aliyun"))
    await repo.insert(_record(backend="aliyun"))
    await repo.insert(_record(backend="local_dict"))

    agg = await repo.aggregate()

    counts = {e.key: e.count for e in agg.by_backend}
    assert counts == {"aliyun": 2, "local_dict": 1}


async def test_aggregate_filters_by_user_id() -> None:
    """Cross-tenant isolation at the aggregate path — same pin as
    list_events but for the stats query."""
    repo = InMemoryModerationEventRepository()
    await repo.insert(_record(user_id="u_alice", verdict="block"))
    await repo.insert(_record(user_id="u_alice", verdict="allow"))
    await repo.insert(_record(user_id="u_bob", verdict="block"))

    agg = await repo.aggregate(user_id="u_alice")

    assert agg.totals.event_count == 2
    assert agg.totals.allow_count == 1
    assert agg.totals.block_count == 1


async def test_aggregate_filters_by_time_window() -> None:
    repo = InMemoryModerationEventRepository()
    too_old = _NOW - timedelta(days=10)
    inside = _NOW - timedelta(days=2)
    too_new = _NOW + timedelta(days=1)
    await repo.insert(_record(verdict="block", created_at=too_old))
    await repo.insert(_record(verdict="allow", created_at=inside))
    await repo.insert(_record(verdict="warn", created_at=too_new))

    agg = await repo.aggregate(since=_NOW - timedelta(days=7), until=_NOW)

    assert agg.totals.event_count == 1
    assert agg.totals.allow_count == 1
    assert agg.totals.block_count == 0  # the old block row is filtered out


async def test_aggregate_totals_match_sum_of_verdicts() -> None:
    """The headline `event_count` MUST equal the sum of the four
    per-verdict counts — dashboard loses trust the moment they
    disagree."""
    repo = InMemoryModerationEventRepository()
    await repo.insert(_record(verdict="allow"))
    await repo.insert(_record(verdict="warn"))
    await repo.insert(_record(verdict="block"))
    await repo.insert(_record(verdict="redirect"))
    await repo.insert(_record(verdict="block"))

    agg = await repo.aggregate()

    by_verdict_sum = sum(e.count for e in agg.by_verdict)
    assert agg.totals.event_count == 5
    assert by_verdict_sum == 5
    assert (
        agg.totals.allow_count
        + agg.totals.warn_count
        + agg.totals.redirect_count
        + agg.totals.block_count
        == agg.totals.event_count
    )


async def test_aggregate_echoes_filters_back() -> None:
    """Bounds + user_id echo back so a serializer doesn't have to
    re-derive them and so `None` surfaces honestly as `null`."""
    repo = InMemoryModerationEventRepository()
    since = _NOW - timedelta(days=7)
    until = _NOW

    agg = await repo.aggregate(since=since, until=until, user_id="u_demo")

    assert agg.since == since
    assert agg.until == until
    assert agg.user_id == "u_demo"
