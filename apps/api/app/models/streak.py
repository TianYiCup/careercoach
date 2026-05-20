"""Streak (consecutive practice-days) persistence — PRD §6.1 / §7.11.

One row per user — the home screen's "你已坚持训练 N 天 🔥" counter
(StreakFire). `POST /v1/sessions` touches it: a session created on a
new Asia/Shanghai calendar day advances `current_days`, a gap resets
it to 1, and `max_days` keeps the all-time best.
"""

from __future__ import annotations

from datetime import date

from sqlalchemy import Date, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class Streak(Base):
    """One row per user — `user_id` is the PK (a user has exactly one
    streak). Plain string, not a FK — same precedent as `sessions` /
    `vibe_logs` (no CASCADE coupling to the users table)."""

    __tablename__ = "streaks"

    user_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    current_days: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    max_days: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")

    # Asia/Shanghai calendar date of the most recent qualifying activity.
    last_active_date: Mapped[date] = mapped_column(Date, nullable=False)

    def __repr__(self) -> str:
        return f"Streak(user_id={self.user_id}, current_days={self.current_days})"
