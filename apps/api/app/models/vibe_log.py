"""Daily vibe check-in persistence — PRD §6.1 / §7.11.

One row per (user, Asia/Shanghai calendar day): `POST /v1/vibe/today`
records how the user feels today, which the home screen uses to pick
教练 K's expression and bias scenario recommendations.

`vibe` is plain text (not Enum) so adding a mood never needs an
ALTER TYPE migration — same precedent as `copilot_sessions.status`.

`logged_date` is the Asia/Shanghai calendar date (CLAUDE.md §6: UTC
storage, Shanghai display — but a *day* boundary only makes sense in
the user's local calendar). The `(user_id, logged_date)` unique
constraint enforces one check-in per day; a re-POST overwrites it.
"""

from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import Date, DateTime, String, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class VibeLog(Base):
    """One row per (user, day) — the user's mood check-in.

    `user_id` is a plain indexed string, not a FK — same precedent as
    `sessions` / `copilot_sessions` (avoids CASCADE silently dropping a
    user's vibe history if their account row gets rebuilt).
    """

    __tablename__ = "vibe_logs"
    __table_args__ = (UniqueConstraint("user_id", "logged_date", name="uq_vibe_logs_user_date"),)

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    user_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)

    # fire / tired / anxious / excited / meh. Plain text — see module docstring.
    vibe: Mapped[str] = mapped_column(String(16), nullable=False)

    # Asia/Shanghai calendar date of the check-in.
    logged_date: Mapped[date] = mapped_column(Date, nullable=False)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    def __repr__(self) -> str:
        return f"VibeLog(user_id={self.user_id}, logged_date={self.logged_date}, vibe={self.vibe})"
