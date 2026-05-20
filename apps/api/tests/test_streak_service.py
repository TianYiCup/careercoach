"""`StreakService` behaviour — the consecutive practice-days counter.

The day-gap arithmetic is the heart of the streak; these tests pin
every transition: first touch, same-day idempotency, consecutive
advance, and gap reset (with `max_days` preserved).
"""

from __future__ import annotations

from datetime import date

from app.services.streak import InMemoryStreakRepository, StreakRecord, StreakService


def _service() -> tuple[StreakService, InMemoryStreakRepository]:
    repo = InMemoryStreakRepository()
    return StreakService(repo=repo), repo


async def test_first_touch_starts_streak_at_one() -> None:
    svc, _ = _service()
    rec = await svc.touch(user_id="u_1", today=date(2026, 5, 20))
    assert rec.current_days == 1
    assert rec.max_days == 1
    assert rec.last_active_date == date(2026, 5, 20)


async def test_second_touch_same_day_is_idempotent() -> None:
    svc, _ = _service()
    await svc.touch(user_id="u_1", today=date(2026, 5, 20))
    rec = await svc.touch(user_id="u_1", today=date(2026, 5, 20))
    assert rec.current_days == 1  # same day must not re-count


async def test_consecutive_days_advance_streak() -> None:
    svc, _ = _service()
    await svc.touch(user_id="u_1", today=date(2026, 5, 20))
    await svc.touch(user_id="u_1", today=date(2026, 5, 21))
    rec = await svc.touch(user_id="u_1", today=date(2026, 5, 22))
    assert rec.current_days == 3
    assert rec.max_days == 3


async def test_gap_resets_current_but_keeps_max() -> None:
    """A missed day breaks the streak — current_days resets to 1, but
    max_days holds the previous best."""
    svc, _ = _service()
    await svc.touch(user_id="u_1", today=date(2026, 5, 20))
    await svc.touch(user_id="u_1", today=date(2026, 5, 21))
    await svc.touch(user_id="u_1", today=date(2026, 5, 22))  # streak = 3
    rec = await svc.touch(user_id="u_1", today=date(2026, 5, 25))  # 2-day gap
    assert rec.current_days == 1
    assert rec.max_days == 3  # all-time best preserved


async def test_get_streak_returns_zero_for_user_who_never_practised() -> None:
    svc, _ = _service()
    rec = await svc.get_streak("u_nobody")
    assert rec.current_days == 0
    assert rec.max_days == 0


async def test_get_streak_reads_back_a_touched_streak() -> None:
    svc, _ = _service()
    await svc.touch(user_id="u_1", today=date(2026, 5, 20))
    await svc.touch(user_id="u_1", today=date(2026, 5, 21))
    rec = await svc.get_streak("u_1")
    assert rec.current_days == 2


async def test_touch_safe_swallows_repo_errors() -> None:
    """touch_safe must never raise — a streak-store hiccup cannot be
    allowed to fail the session-create that calls it."""

    class _BoomRepo:
        async def get(self, user_id: str) -> StreakRecord | None:
            raise RuntimeError("streak store down")

        async def upsert(self, record: StreakRecord) -> None:  # pragma: no cover
            raise AssertionError("not reached — get() fails first")

    svc = StreakService(repo=_BoomRepo())
    # Must complete without raising.
    await svc.touch_safe(user_id="u_1", today=date(2026, 5, 20))
