"""`InMemoryWeaknessRepository` behaviour — the weakness-profile store.

`increment` is an upsert keyed on (user_id, tag): the first hit inserts,
later hits accumulate `frequency`. `frequency` floors at 0.
"""

from __future__ import annotations

from datetime import UTC, datetime

from app.services.weakness.repository import InMemoryWeaknessRepository

_NOW = datetime(2026, 5, 20, 8, 0, tzinfo=UTC)
_LATER = datetime(2026, 5, 21, 8, 0, tzinfo=UTC)


async def test_increment_inserts_on_first_hit() -> None:
    repo = InMemoryWeaknessRepository()
    await repo.increment(user_id="u_1", tag="过早让步", delta=1, now=_NOW, fresh_id="wk_a")
    rows = await repo.list_for_user("u_1")
    assert len(rows) == 1
    assert rows[0].tag == "过早让步"
    assert rows[0].frequency == 1
    assert rows[0].id == "wk_a"


async def test_increment_accumulates_and_keeps_id() -> None:
    repo = InMemoryWeaknessRepository()
    await repo.increment(user_id="u_1", tag="过早让步", delta=1, now=_NOW, fresh_id="wk_first")
    await repo.increment(user_id="u_1", tag="过早让步", delta=2, now=_LATER, fresh_id="wk_second")
    rows = await repo.list_for_user("u_1")
    assert len(rows) == 1  # one row per (user, tag)
    assert rows[0].frequency == 3  # 1 + 2
    assert rows[0].id == "wk_first"  # original id retained
    assert rows[0].last_seen == _LATER  # timestamp advances


async def test_increment_floors_frequency_at_zero() -> None:
    """A negative delta ('improved') cannot drive frequency below 0."""
    repo = InMemoryWeaknessRepository()
    await repo.increment(user_id="u_1", tag="情绪外露", delta=1, now=_NOW, fresh_id="wk_a")
    await repo.increment(user_id="u_1", tag="情绪外露", delta=-5, now=_LATER, fresh_id="wk_b")
    rows = await repo.list_for_user("u_1")
    assert rows[0].frequency == 0


async def test_list_for_user_sorted_by_frequency_desc() -> None:
    repo = InMemoryWeaknessRepository()
    await repo.increment(user_id="u_1", tag="过早让步", delta=1, now=_NOW, fresh_id="wk_a")
    await repo.increment(user_id="u_1", tag="被绕话题", delta=5, now=_NOW, fresh_id="wk_b")
    await repo.increment(user_id="u_1", tag="情绪外露", delta=3, now=_NOW, fresh_id="wk_c")
    rows = await repo.list_for_user("u_1")
    assert [r.tag for r in rows] == ["被绕话题", "情绪外露", "过早让步"]


async def test_list_for_user_isolates_users_and_is_empty_when_absent() -> None:
    repo = InMemoryWeaknessRepository()
    await repo.increment(user_id="u_1", tag="过早让步", delta=1, now=_NOW, fresh_id="wk_a")
    assert await repo.list_for_user("u_2") == []
