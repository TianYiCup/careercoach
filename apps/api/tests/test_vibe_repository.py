"""`InMemoryVibeRepository` behaviour — the daily mood check-in store.

`set_today` is an upsert keyed on (user_id, logged_date): a re-check-in
overwrites the mood but keeps the original row id.
"""

from __future__ import annotations

from datetime import UTC, date, datetime

from app.services.vibe.repository import InMemoryVibeRepository, VibeLogRecord, VibeType


def _record(
    *,
    user_id: str = "u_1",
    vibe: VibeType = "fire",
    day: date | None = None,
    rec_id: str = "vibe_aaaa1111",
) -> VibeLogRecord:
    return VibeLogRecord(
        id=rec_id,
        user_id=user_id,
        vibe=vibe,
        logged_date=day or date(2026, 5, 20),
        created_at=datetime(2026, 5, 20, 6, 0, tzinfo=UTC),
    )


async def test_set_today_then_get_returns_record() -> None:
    repo = InMemoryVibeRepository()
    rec = _record()
    await repo.set_today(rec)
    assert await repo.get_for_date("u_1", date(2026, 5, 20)) == rec


async def test_get_for_date_returns_none_when_no_checkin() -> None:
    repo = InMemoryVibeRepository()
    assert await repo.get_for_date("u_1", date(2026, 5, 20)) is None


async def test_set_today_twice_same_day_overwrites_mood_keeps_id() -> None:
    """A re-check-in overwrites the mood but keeps the original row id —
    one row per (user, day) with a stable identity."""
    repo = InMemoryVibeRepository()
    await repo.set_today(_record(vibe="fire", rec_id="vibe_original"))
    await repo.set_today(_record(vibe="tired", rec_id="vibe_different"))
    got = await repo.get_for_date("u_1", date(2026, 5, 20))
    assert got is not None
    assert got.vibe == "tired"
    assert got.id == "vibe_original"  # original id retained on overwrite


async def test_set_today_isolates_users_and_dates() -> None:
    repo = InMemoryVibeRepository()
    await repo.set_today(_record(user_id="u_1", day=date(2026, 5, 20), vibe="fire"))
    await repo.set_today(_record(user_id="u_2", day=date(2026, 5, 20), vibe="meh"))
    await repo.set_today(_record(user_id="u_1", day=date(2026, 5, 21), vibe="excited"))

    u1_20 = await repo.get_for_date("u_1", date(2026, 5, 20))
    u2_20 = await repo.get_for_date("u_2", date(2026, 5, 20))
    u1_21 = await repo.get_for_date("u_1", date(2026, 5, 21))
    assert u1_20 is not None and u1_20.vibe == "fire"
    assert u2_20 is not None and u2_20.vibe == "meh"
    assert u1_21 is not None and u1_21.vibe == "excited"
