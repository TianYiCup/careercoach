"""Per-session aggregated scorecard (PRD §7.4 / §7.9).

Written once on `POST /v1/sessions/{id}/end` after the aggregator
runs the LLM summary; read on every `POST /v1/sharecards/session/{id}`
to render the card. Caching this row matters: without it, every
re-render would re-run the LLM summary (costing money + introducing
non-determinism).

One row per session. FK to `sessions` cascades on delete so wiping a
session also drops its score. Separate table (not columns on
`sessions`) because:

  * The session row tracks lifecycle (created, ended), the score row
    tracks the OUTPUT of the session — different concept lifecycles.
  * `aggregate_session_score()` may evolve; adding/removing score
    fields without touching the sessions table keeps the diff
    smaller and the migration safer.
  * The existing `InMemorySessionScoreRepository` already keeps these
    as separate dicts, so this just lifts the in-memory shape to PG.

Note: `user_caption` is NOT stored here. It's a route-time override
the renderer takes via `dataclass.replace(score, user_caption=...)`,
never persisted to the score row.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class SessionScore(Base):
    """Cached aggregator output for one finished session."""

    __tablename__ = "session_scores"

    # 1:1 with sessions — same id is both PK and FK. CASCADE so wiping
    # a session drops its score together with its turns.
    session_id: Mapped[str] = mapped_column(
        String(32),
        ForeignKey("sessions.session_id", ondelete="CASCADE"),
        primary_key=True,
    )

    # Denormalised from the scenarios catalog at /end time so the card
    # keeps rendering correctly even if the catalog entry is later
    # edited or removed.
    scenario_title: Mapped[str] = mapped_column(String(64), nullable=False)
    persona_title: Mapped[str] = mapped_column(String(64), nullable=False)

    # Five-dim score — same shape the renderer takes in
    # `SessionCardData`.
    aura: Mapped[int] = mapped_column(Integer, nullable=False)
    logic: Mapped[int] = mapped_column(Integer, nullable=False)
    emotion: Mapped[int] = mapped_column(Integer, nullable=False)
    professionalism: Mapped[int] = mapped_column(Integer, nullable=False)
    goal_achieve: Mapped[int] = mapped_column(Integer, nullable=False)

    # Verdict literal (shenfeng / guolu / fanche). Plain text rather
    # than a typed Enum so adding a verdict doesn't need an ALTER TYPE
    # migration — same call as `sessions.status` and `turns.verdict`.
    result: Mapped[str] = mapped_column(String(16), nullable=False)

    # K's praise; bounded but generous since narrative copy is short.
    highlights: Mapped[str] = mapped_column(String(500), nullable=False)

    # Indexed because weekly + wrapped both filter by this window.
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        index=True,
    )

    def __repr__(self) -> str:
        return f"SessionScore(session_id={self.session_id}, result={self.result})"
