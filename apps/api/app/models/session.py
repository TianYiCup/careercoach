"""Sandbox session row (PRD §7.4).

One row per `POST /v1/sessions`. Tracks the session lifecycle from
creation through ending. Turn history is intentionally NOT stored
here — turns get their own table in PR 4b alongside SSE streaming, so
end-of-session aggregation and per-turn replay live in distinct shapes.

The row carries the inputs that scored the session (scenario / persona
/ user goal) plus a `status` and an `ended_at` so we can answer "is
this session still open?" without joining the turns table. The 5-dim
score itself lives in `session_scores` (added in PR 4b) — by that
point the Judge node will have populated it.

v0 lifecycle:
  active  — POST /sessions just landed; /turns is allowed
  ended   — POST /sessions/{id}/end fired; /sharecards/session/{id}
            can now resolve to a real card
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import JSON, DateTime, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class Session(Base):
    """Audit row for one sandbox practice session."""

    __tablename__ = "sessions"

    # `ses_<16-hex>` is what the frontend hands back on every subsequent
    # turn or end call. Making it the PK keeps every lookup one round-trip.
    session_id: Mapped[str] = mapped_column(String(32), primary_key=True)

    # v0 doesn't have auth wired yet; the service layer fills this with
    # an anonymous sentinel so the row still carries useful provenance.
    user_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)

    # Echoes the request payload (PRD §7.4 CreateSessionRequest).
    mode: Mapped[str] = mapped_column(String(16), nullable=False)
    scenario_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    persona_id: Mapped[str] = mapped_column(String(64), nullable=False)
    user_goal: Mapped[str] = mapped_column(String(200), nullable=False)

    # `active` / `ended`. Plain text rather than a typed Enum so adding
    # a state (paused, expired) doesn't need an ALTER TYPE migration.
    status: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
        server_default="active",
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    # NULL while the session is still active; set by POST /end.
    ended_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    # Character Engine L3 — live 6-dim mood vector. Seeded equal to the
    # scenario's static character_vector at create time, then mutated by
    # the MoodArbiter after every user turn. Stored as JSON to mirror
    # `scenarios.character_vector`; the same `CharacterVector.from_dict`
    # parser round-trips both. Server default is the neutral baseline so
    # an in-flight migration doesn't leave rows with a NULL mood and
    # crash the prompt builder.
    mood_vector: Mapped[dict[str, int]] = mapped_column(
        JSON,
        nullable=False,
        default=lambda: {
            "aggression": 50,
            "empathy": 50,
            "control": 50,
            "honesty": 50,
            "stability": 50,
            "power_gap": 50,
        },
    )

    def __repr__(self) -> str:
        return f"Session(session_id={self.session_id}, status={self.status})"
