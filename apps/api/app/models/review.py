"""Review-mode (复盘师) persistence — PRD §7.6.

Two tables in one module because they share lifetime: a `ReviewUpload`
row is meaningless without its child `ReviewTurn` rows, and the only
read pattern is "load one upload + all its turns ordered by idx" —
exactly the join `apps/api/app/services/review/repository.py` runs.

Why a separate child table at all
---------------------------------
The reviewer LangGraph chain (A-10) emits a list of per-turn verdicts;
the size is bounded by US-C1 L2 (≤ 50 turns after segmentation) but
varies per upload. Storing turns as a JSON column on `review_uploads`
would make the verdict-distribution queries that drive the弱点档案
(US-C3, post-MVP) impossible without JSON traversal — exact same
reason `turns` is its own table beside `sessions`.

Schema flattening note
----------------------
`reason` and `better` are nullable plain columns instead of a JSON
`{reason, better}` blob. Same call as `turn.coach_hint_*` — the
fixed shape doesn't justify the JSON tax, and the route layer just
maps null → omitted-key in the response.

`summary_top_failures` and `summary_improvements` use Postgres
`ARRAY(String)` rather than child rows. Both lists are bounded
(≤ 3 items per PRD §3.3 US-C2) and never queried individually, so
ARRAY keeps the row self-contained without losing analytics ability.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, Float, ForeignKey, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class ReviewUpload(Base):
    """One row per `POST /v1/review/uploads`.

    Lifecycle:
      processing → done   (reviewer chain finished, turns + summary populated)
      processing → failed (chain errored; client may re-upload)

    `text` is unbounded TEXT even though the API caps input at 5000
    chars — Pydantic enforces the cap at the boundary, the column
    stays untyped so future v2 image/audio uploads can land their
    OCR/ASR transcript here without an ALTER COLUMN.
    """

    __tablename__ = "review_uploads"

    upload_id: Mapped[str] = mapped_column(String(32), primary_key=True)

    # Plain string (not FK) — matches the `sessions.user_id` precedent.
    # The user table is small and stable but we don't want a CASCADE
    # path that would silently drop someone's review history if their
    # account row got rebuilt.
    user_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)

    text: Mapped[str] = mapped_column(Text, nullable=False)

    # `processing` / `done` / `failed`. Plain text rather than typed
    # Enum so adding a state doesn't need an ALTER TYPE migration —
    # same call as `sessions.status`.
    status: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
        server_default="processing",
    )

    # NULL until the reviewer chain finishes. Populated together
    # with the turns + summary lists in a single repo call.
    summary_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    summary_top_failures: Mapped[list[str]] = mapped_column(
        ARRAY(String(64)),
        nullable=False,
        server_default="{}",
    )
    summary_improvements: Mapped[list[str]] = mapped_column(
        ARRAY(String(200)),
        nullable=False,
        server_default="{}",
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    def __repr__(self) -> str:
        return f"ReviewUpload(upload_id={self.upload_id}, status={self.status})"


class ReviewTurn(Base):
    """One analysed line inside a review upload.

    Composite PK `(upload_id, turn_idx)` so re-running the reviewer
    chain on the same upload (idempotent overwrite path in
    `update_result`) doesn't need to track per-turn synthetic ids —
    the repo just DELETEs by upload_id and bulk-INSERTs the new set.
    """

    __tablename__ = "review_turns"

    upload_id: Mapped[str] = mapped_column(
        String(32),
        ForeignKey("review_uploads.upload_id", ondelete="CASCADE"),
        primary_key=True,
        index=True,
    )
    turn_idx: Mapped[int] = mapped_column(Integer, primary_key=True)

    # `user` / `opponent`. Plain text — only two values today, but
    # PRD §3.3 US-C1 hints at speaker-diarization expanding this set
    # in v2 (multiple speakers in group-chat screenshots).
    speaker: Mapped[str] = mapped_column(String(16), nullable=False)

    content: Mapped[str] = mapped_column(Text, nullable=False)

    # `win` / `neutral` / `lose` — the three colors PRD §3.3 US-C2
    # specifies. Plain text for the same migrate-cheaply reason.
    verdict: Mapped[str] = mapped_column(String(16), nullable=False)

    # Only populated for `lose` verdict turns. PRD §3.3 caps reason
    # ≤ 50 字 and better ≤ 80 字 — column widths are 10x to absorb
    # LLM over-spend without truncation.
    reason: Mapped[str | None] = mapped_column(String(500), nullable=True)
    better: Mapped[str | None] = mapped_column(String(500), nullable=True)

    def __repr__(self) -> str:
        return f"ReviewTurn(upload_id={self.upload_id}, turn_idx={self.turn_idx})"
