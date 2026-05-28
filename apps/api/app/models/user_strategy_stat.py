"""User strategy-stats persistence — Character Engine L5.

One row per (user, strategy). Each time coach K reads the user's turn
(L8), the matching row's `count` and the per-effect tally (`good` /
`mixed` / `poor`) accumulate. From these the profile derives:

  * which strategies the user leans on (high `count`);
  * whether each lands (win rate = good / count);
  * the over-relied-but-failing strategy that L5 uses to harden the
    opponent's countering dimensions.

`strategy` is one of the closed `coach_strategy.STRATEGY_LABELS` keys,
stored as plain text (no Enum, matching the `weaknesses.tag` precedent)
so adding a strategy never needs an ALTER TYPE migration. `user_id` is
a plain string, not a FK — same no-CASCADE precedent as weaknesses /
sessions / streaks.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, Integer, String, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class UserStrategyStat(Base):
    """Aggregated usage + effectiveness of one strategy for one user."""

    __tablename__ = "user_strategy_stats"
    __table_args__ = (
        UniqueConstraint("user_id", "strategy", name="uq_user_strategy_stats_user_strategy"),
    )

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    user_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    strategy: Mapped[str] = mapped_column(String(32), nullable=False)
    count: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    # Per-effect tallies. `good + mixed + poor == count` is the invariant
    # a test guards; win rate is derived as good / count at read time.
    good: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    mixed: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    poor: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    last_seen: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    def __repr__(self) -> str:
        return (
            f"UserStrategyStat(user_id={self.user_id}, "
            f"strategy={self.strategy}, count={self.count})"
        )
