"""Long-term episodic memory persistence — Character Engine L6.

One row per (user, scenario): a compact memory of how the user's last
practice of that scenario went, so the opponent can recall it next time
("上次你也是这套说辞，结果没拿下"). `visit_count` accumulates across
sessions; the rest is overwritten with the latest episode at session end.

This is structured episodic recall, not vector-semantic memory — keyed
on (user, scenario), no embeddings. A pgvector layer for cross-scenario
"similar situations" is a later epic. `user_id` is a plain string, not
a FK (same no-CASCADE precedent as weaknesses / user_strategy_stats).
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, Integer, String, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class SessionEpisode(Base):
    """The opponent's memory of one user's history in one scenario."""

    __tablename__ = "session_episodes"
    __table_args__ = (
        UniqueConstraint("user_id", "scenario_id", name="uq_session_episodes_user_scenario"),
    )

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    user_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    scenario_id: Mapped[str] = mapped_column(String(64), nullable=False)
    # How many times the user has finished a session in this scenario.
    visit_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    # Last outcome verdict (shenfeng / guolu / fanche) + the one-line
    # failures takeaway from that session's score — what the opponent
    # "remembers" about how the user did.
    last_result: Mapped[str] = mapped_column(String(16), nullable=False)
    last_takeaway: Mapped[str] = mapped_column(String(300), nullable=False, server_default="")
    last_seen: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    def __repr__(self) -> str:
        return (
            f"SessionEpisode(user_id={self.user_id}, "
            f"scenario_id={self.scenario_id}, visit_count={self.visit_count})"
        )
