"""Copilot (副驾 / live-coaching) session persistence — PRD §7.5.

One row per `POST /v1/copilot/sessions`. The actual realtime
audio chunks + hint stream live on the (future) WebSocket
endpoint — this table just tracks the session lifecycle so:
  * the client can reconnect to an existing session (`copilot_id`)
  * audit / billing has a record of how long each session lasted
  * the future hint chain (A-17/A-18) can persist transcripts +
    coach turns alongside the session row

Lifecycle
---------
  pending   → POST returned; client hasn't connected to the WS yet.
  connected → WS handshake completed (A-17 will flip this).
  ended     → WS closed cleanly or timed out (A-17/A-18).

`status` is plain text (not Enum) so adding a state never needs an
ALTER TYPE migration — same call as `sessions.status` and
`review_uploads.status`.

`privacy_level` is also plain text:
  * `standard` → cloud ASR
  * `high`     → on-device ASR + redaction (US-B3, post-MVP)
The future ASR adapter routes on this value; persisting it lets the
WS handler pick the right backend without re-reading the request body.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class CopilotSession(Base):
    """One row per `POST /v1/copilot/sessions`.

    `user_id` is a plain indexed string, not a FK — same precedent as
    `sessions` / `review_uploads` (avoids CASCADE silently dropping a
    user's copilot history if their account row gets rebuilt).
    """

    __tablename__ = "copilot_sessions"

    copilot_id: Mapped[str] = mapped_column(String(32), primary_key=True)
    user_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)

    # Free-text — PRD §7.5 caps at 200 chars (`CreateCopilotSessionRequest`
    # enforces it at the API boundary). Persisting the hint lets the
    # future hint chain prime the LLM context without re-prompting.
    scenario_hint: Mapped[str] = mapped_column(String(200), nullable=False)

    # `standard` / `high`. Plain text — see module docstring.
    privacy_level: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
        server_default="standard",
    )

    # `pending` / `connected` / `ended`. A-15 only writes `pending`;
    # A-17 will flip on WS handshake / disconnect.
    status: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
        server_default="pending",
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    connected_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    ended_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    def __repr__(self) -> str:
        return f"CopilotSession(copilot_id={self.copilot_id}, status={self.status})"
