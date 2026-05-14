"""Per-turn record inside a sandbox session (PRD §7.4).

One row per `POST /v1/sessions/{id}/turns` — the user said something,
the opponent replied, Coach K gave three hints, the Judge node scored
the turn. Persisting these is what makes `/end` aggregation honest
(PR 4c) and what survives an uvicorn restart so mid-session refresh
doesn't lose history.

Schema flattening note
----------------------
`CoachHintTrio` and `TurnScore` are stored as discrete columns rather
than JSON. Flattening keeps analytics queries cheap — "what's the
verdict distribution this week" is a simple GROUP BY instead of a
JSON traversal — and the small fixed shape doesn't justify the JSON
flexibility tax.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class Turn(Base):
    """One user/opponent exchange inside a session."""

    __tablename__ = "turns"

    # `t_<8-hex>` matches the MSW mock + SSE schema example.
    turn_id: Mapped[str] = mapped_column(String(16), primary_key=True)

    # ON DELETE CASCADE so wiping a session takes its turns with it —
    # the audit story for moderation events lives in a different table.
    session_id: Mapped[str] = mapped_column(
        String(32),
        ForeignKey("sessions.session_id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    # User input is bounded by `TurnRequest.content` at 2000 chars; the
    # opponent reply is bounded by `_ROLEPLAY_PROMPT`'s `≤ 80 字` hint
    # but we accept a longer column because LLMs occasionally over-spend.
    user_content: Mapped[str] = mapped_column(String(2000), nullable=False)
    opponent_reply: Mapped[str] = mapped_column(String(1000), nullable=False)

    # `CoachHintTrio` flattened — three 30-char-ish strings, generous
    # column width keeps a slightly long hint from truncating.
    coach_hint_safe: Mapped[str] = mapped_column(String(200), nullable=False)
    coach_hint_aggressive: Mapped[str] = mapped_column(String(200), nullable=False)
    coach_hint_humor: Mapped[str] = mapped_column(String(200), nullable=False)

    # `TurnScore` flattened — verdict is one of shenfeng / guolu /
    # fanche, stored as plain text so adding a verdict doesn't need an
    # ALTER TYPE migration (same call as `sessions.status`).
    verdict: Mapped[str] = mapped_column(String(16), nullable=False)
    rating: Mapped[int] = mapped_column(Integer, nullable=False)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    def __repr__(self) -> str:
        return f"Turn(turn_id={self.turn_id}, session_id={self.session_id})"
