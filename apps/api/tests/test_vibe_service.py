"""`VibeService` behaviour — records the caller's daily mood (PRD §7.11)."""

from __future__ import annotations

from datetime import date, datetime
from zoneinfo import ZoneInfo

from app.services.vibe import InMemoryVibeRepository, VibeService


def _service() -> tuple[VibeService, InMemoryVibeRepository]:
    repo = InMemoryVibeRepository()
    return VibeService(repo=repo), repo


async def test_set_today_vibe_persists_and_returns_record() -> None:
    svc, repo = _service()
    rec = await svc.set_today_vibe(user_id="u_1", vibe="fire", today=date(2026, 5, 20))
    assert rec.user_id == "u_1"
    assert rec.vibe == "fire"
    assert rec.logged_date == date(2026, 5, 20)
    assert rec.id.startswith("vibe_")
    # Persisted — readable back at the same date.
    assert await repo.get_for_date("u_1", date(2026, 5, 20)) == rec


async def test_set_today_vibe_recheckin_overwrites_mood() -> None:
    svc, repo = _service()
    await svc.set_today_vibe(user_id="u_1", vibe="anxious", today=date(2026, 5, 20))
    await svc.set_today_vibe(user_id="u_1", vibe="excited", today=date(2026, 5, 20))
    stored = await repo.get_for_date("u_1", date(2026, 5, 20))
    assert stored is not None
    assert stored.vibe == "excited"


async def test_set_today_vibe_defaults_to_shanghai_calendar_date() -> None:
    """With `today` omitted the service stamps the Asia/Shanghai
    calendar date (CLAUDE.md §6) — not the UTC date, which can differ
    by one near the day boundary."""
    svc, _ = _service()
    rec = await svc.set_today_vibe(user_id="u_1", vibe="meh")
    assert rec.logged_date == datetime.now(ZoneInfo("Asia/Shanghai")).date()
