"""ShareCard persistence row (PRD §6.2 / §7.9).

One row per successfully-rendered card. Storing the row lets us:
  * answer "how did this card get generated?" for support
  * regenerate signed URLs when storage permissions rotate
  * rate-limit `/sharecards/*` per user (PRD §7.12) by counting rows

The actual PNG bytes live in `ShareCardStorage` (local FS today, S3
later); the DB row only carries the storage key plus the metadata
needed to rebuild the public response.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class ShareCard(Base):
    """Audit + retrieval row for one generated share card."""

    __tablename__ = "sharecards"

    # `card_<16-hex>` is the public id surfaced by ShareCardResponse and
    # used as the storage object key — making it the PK keeps the
    # render→persist→respond flow one round-trip.
    card_id: Mapped[str] = mapped_column(String(32), primary_key=True)

    # `session` / `weekly` / `wrapped` — stored as plain text so we can
    # add a card type in code without an ALTER TYPE migration.
    card_type: Mapped[str] = mapped_column(String(16), nullable=False, index=True)

    user_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)

    # Session-typed cards reference the source session; weekly / wrapped
    # leave this nullable (they aggregate many sessions).
    session_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)

    # Where the bytes live. For `local_fs` this is a relative path under
    # the configured storage root; for S3 it would be the object key.
    # The matching public URL is rebuilt on every read so we can rotate
    # the host without a migration.
    storage_key: Mapped[str] = mapped_column(String(255), nullable=False)
    storage_backend: Mapped[str] = mapped_column(String(32), nullable=False)

    # User-edited caption baked into the PNG, post-moderation. Stored so
    # support can answer "why does my card say X?" — empty for cards
    # that used K's default verdict copy.
    user_caption: Mapped[str | None] = mapped_column(String(120), nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    def __repr__(self) -> str:
        return f"ShareCard(card_id={self.card_id}, type={self.card_type}, user={self.user_id})"
