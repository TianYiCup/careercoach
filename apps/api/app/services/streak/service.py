"""Streak service — the consecutive practice-days counter (PRD §7.11).

`touch` registers practice activity (called when a session starts);
`touch_safe` is the best-effort wrapper the session-create route uses
so a streak-store hiccup never fails the create. `get_streak` is the
read side behind `GET /v1/streak`.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import structlog

from app.services.streak.repository import StreakRecord, StreakRepository

logger = structlog.get_logger(__name__)

# Asia/Shanghai is UTC+8 year-round (no DST). A streak counts *calendar
# days*, which only make sense in the user's local calendar — CLAUDE.md
# §6 (UTC storage, Shanghai display).
_SHANGHAI_TZ = ZoneInfo("Asia/Shanghai")


def _shanghai_today() -> date:
    return datetime.now(_SHANGHAI_TZ).date()


def _advance(prev: StreakRecord | None, *, user_id: str, today: date) -> StreakRecord:
    """Pure streak transition. Separate from the repo so the day-gap
    arithmetic is unit-testable on its own:

      * no prior row         → start the streak at 1
      * already active today → unchanged (idempotent within a day)
      * active yesterday     → +1 consecutive day
      * older gap            → the streak broke, reset to 1

    `max_days` only ever climbs. Returns `prev` unchanged (same object)
    when nothing moves, so the caller can skip a needless write.
    """
    if prev is None:
        return StreakRecord(user_id=user_id, current_days=1, max_days=1, last_active_date=today)
    if prev.last_active_date >= today:
        # Already counted today — or a clock skew put last_active ahead.
        # Never double-count, never walk the streak backwards.
        return prev
    # A consecutive day advances the streak; a wider gap breaks it back
    # to 1 (a same-day touch was already handled above).
    yesterday = today - timedelta(days=1)
    current = prev.current_days + 1 if prev.last_active_date == yesterday else 1
    return StreakRecord(
        user_id=user_id,
        current_days=current,
        max_days=max(prev.max_days, current),
        last_active_date=today,
    )


class StreakService:
    """Owns the consecutive practice-days counter."""

    def __init__(self, *, repo: StreakRepository) -> None:
        self._repo = repo

    async def touch(self, *, user_id: str, today: date | None = None) -> StreakRecord:
        """Register practice activity for `user_id` today; return the
        updated streak. `today` is injectable so tests pin the day."""
        day = today or _shanghai_today()
        prev = await self._repo.get(user_id)
        updated = _advance(prev, user_id=user_id, today=day)
        if updated is not prev:
            await self._repo.upsert(updated)
        return updated

    async def touch_safe(self, *, user_id: str, today: date | None = None) -> None:
        """Best-effort `touch` — swallows + logs any error so a streak
        store hiccup never fails the caller (the session-create route).
        Mirrors the moderation event-sink's 'audit must never block the
        response' contract."""
        try:
            await self.touch(user_id=user_id, today=today)
        except Exception:
            logger.exception("streak_touch_failed", user_id=user_id)

    async def get_streak(self, user_id: str) -> StreakRecord:
        """Read the caller's streak. A user who has never practised gets
        a zero streak rather than a 404 — the home counter shows 0."""
        existing = await self._repo.get(user_id)
        if existing is not None:
            return existing
        return StreakRecord(
            user_id=user_id,
            current_days=0,
            max_days=0,
            last_active_date=_shanghai_today(),
        )
