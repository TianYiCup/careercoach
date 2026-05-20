"""`InMemoryStreakRepository` behaviour — the per-user streak store."""

from __future__ import annotations

from datetime import date

from app.services.streak.repository import InMemoryStreakRepository, StreakRecord


def _record(
    *,
    user_id: str = "u_1",
    current: int = 3,
    mx: int = 5,
    day: date | None = None,
) -> StreakRecord:
    return StreakRecord(
        user_id=user_id,
        current_days=current,
        max_days=mx,
        last_active_date=day or date(2026, 5, 20),
    )


async def test_get_returns_none_when_absent() -> None:
    repo = InMemoryStreakRepository()
    assert await repo.get("u_1") is None


async def test_upsert_then_get_roundtrips() -> None:
    repo = InMemoryStreakRepository()
    rec = _record()
    await repo.upsert(rec)
    assert await repo.get("u_1") == rec


async def test_upsert_replaces_existing_row() -> None:
    repo = InMemoryStreakRepository()
    await repo.upsert(_record(current=3, mx=5))
    await repo.upsert(_record(current=4, mx=5, day=date(2026, 5, 21)))
    got = await repo.get("u_1")
    assert got is not None
    assert got.current_days == 4
    assert got.last_active_date == date(2026, 5, 21)


async def test_upsert_isolates_users() -> None:
    repo = InMemoryStreakRepository()
    await repo.upsert(_record(user_id="u_1", current=3))
    await repo.upsert(_record(user_id="u_2", current=9))
    u1 = await repo.get("u_1")
    u2 = await repo.get("u_2")
    assert u1 is not None and u1.current_days == 3
    assert u2 is not None and u2.current_days == 9
