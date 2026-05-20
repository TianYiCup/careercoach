"""Weakness-profile persistence — PRD §6.1 / §7.7 (弱点画像 US-C3).

One row per (user, weakness tag). `POST /v1/sessions/{id}/end` folds
its per-tag deltas in (frequency accumulates); `GET /v1/users/me/
weaknesses` reads them back, highest-frequency first.

`tag` is plain text (the ~20-tag taxonomy lives in product copy, not
an Enum) so adding a tag never needs an ALTER TYPE migration.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, Integer, String, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class Weakness(Base):
    """One tracked communication weakness for a user.

    `user_id` is a plain string, not a FK — same precedent as
    `sessions` / `vibe_logs` / `streaks` (no CASCADE coupling to the
    users table). The `(user_id, tag)` unique constraint enforces one
    row per tag; session-end accumulates `frequency` in place.
    """

    __tablename__ = "weaknesses"
    __table_args__ = (UniqueConstraint("user_id", "tag", name="uq_weaknesses_user_tag"),)

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    user_id: Mapped[str] = mapped_column(String(64), nullable=False)
    tag: Mapped[str] = mapped_column(String(64), nullable=False)
    frequency: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    last_seen: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    def __repr__(self) -> str:
        return f"Weakness(user_id={self.user_id}, tag={self.tag}, frequency={self.frequency})"
