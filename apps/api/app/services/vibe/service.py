"""Vibe service — records the caller's daily mood (PRD §7.11).

One check-in per (user, Asia/Shanghai calendar day); a re-check-in
overwrites the mood. The recorded vibe biases the home screen's 教练 K
expression + scenario recommendations (consumed by a later endpoint).
"""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime
from zoneinfo import ZoneInfo

import structlog

from app.services.vibe.repository import VibeLogRecord, VibeRepository, VibeType

logger = structlog.get_logger(__name__)

# Asia/Shanghai is UTC+8 year-round (China abolished DST in 1991). A
# vibe is "today's mood", and a *day* boundary only makes sense in the
# user's local calendar — CLAUDE.md §6 (UTC storage, Shanghai display).
_SHANGHAI_TZ = ZoneInfo("Asia/Shanghai")


def _shanghai_today() -> date:
    return datetime.now(_SHANGHAI_TZ).date()


class VibeService:
    """Owns the daily vibe check-in."""

    def __init__(self, *, repo: VibeRepository) -> None:
        self._repo = repo

    async def set_today_vibe(
        self,
        *,
        user_id: str,
        vibe: VibeType,
        today: date | None = None,
    ) -> VibeLogRecord:
        """Record (or overwrite) the caller's mood for today.

        `today` is injectable so tests pin the calendar day; production
        always derives it from the Asia/Shanghai clock.
        """
        logged_date = today or _shanghai_today()
        record = VibeLogRecord(
            id=f"vibe_{uuid.uuid4().hex[:8]}",
            user_id=user_id,
            vibe=vibe,
            logged_date=logged_date,
            created_at=datetime.now(UTC),
        )
        await self._repo.set_today(record)
        logger.info(
            "vibe_recorded",
            user_id=user_id,
            vibe=vibe,
            logged_date=logged_date.isoformat(),
        )
        return record
