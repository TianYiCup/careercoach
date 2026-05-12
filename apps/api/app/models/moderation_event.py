"""ModerationEvent audit row (PRD §6.2).

One row per call to `ModerationService.check`. We store a SHA-256 of
the content rather than the raw text — the audit log itself becomes a
red-line corpus otherwise, and raw retention deserves its own privacy
review before we ship it.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import JSON, DateTime, Float, Integer, String, func
from sqlalchemy.dialects.postgresql import UUID as PgUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class ModerationEvent(Base):
    """Audit record for one moderation decision."""

    __tablename__ = "moderation_events"

    id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    user_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    session_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)

    # We hash content rather than store it. Hex SHA-256 is always 64 chars.
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    content_length: Mapped[int] = mapped_column(Integer, nullable=False)

    # Stored as plain strings (matching the Literal in app.schemas.moderation)
    # rather than native enums — keeping enums out of the schema means we can
    # add a category in code without an ALTER TYPE migration each time.
    context: Mapped[str] = mapped_column(String(32), nullable=False)
    verdict: Mapped[str] = mapped_column(String(16), nullable=False)

    # JSON list of category strings. Postgres-only `ARRAY(TEXT)` would also
    # work but JSON keeps the schema portable for sqlite-backed unit tests.
    categories: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)

    score: Mapped[float] = mapped_column(Float, nullable=False)
    backend: Mapped[str] = mapped_column(String(32), nullable=False)

    trace_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    def __repr__(self) -> str:
        return f"ModerationEvent(id={self.id!s}, verdict={self.verdict}, backend={self.backend})"
