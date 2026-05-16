"""create review_uploads + review_turns tables

Revision ID: f3a7c91d4b62
Revises: e92f7d4a8c61
Create Date: 2026-05-16 10:00:00.000000+00:00

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import ARRAY

revision: str = "f3a7c91d4b62"
down_revision: str | None = "e92f7d4a8c61"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "review_uploads",
        sa.Column("upload_id", sa.String(length=32), nullable=False),
        sa.Column("user_id", sa.String(length=64), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column(
            "status",
            sa.String(length=16),
            server_default="processing",
            nullable=False,
        ),
        sa.Column("summary_score", sa.Float(), nullable=True),
        sa.Column(
            "summary_top_failures",
            ARRAY(sa.String(length=64)),
            server_default="{}",
            nullable=False,
        ),
        sa.Column(
            "summary_improvements",
            ARRAY(sa.String(length=200)),
            server_default="{}",
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("upload_id", name=op.f("pk_review_uploads")),
    )
    op.create_index(
        op.f("ix_review_uploads_user_id"),
        "review_uploads",
        ["user_id"],
        unique=False,
    )

    op.create_table(
        "review_turns",
        sa.Column("upload_id", sa.String(length=32), nullable=False),
        sa.Column("turn_idx", sa.Integer(), nullable=False),
        sa.Column("speaker", sa.String(length=16), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("verdict", sa.String(length=16), nullable=False),
        sa.Column("reason", sa.String(length=500), nullable=True),
        sa.Column("better", sa.String(length=500), nullable=True),
        sa.ForeignKeyConstraint(
            ["upload_id"],
            ["review_uploads.upload_id"],
            ondelete="CASCADE",
            name=op.f("fk_review_turns_upload_id_review_uploads"),
        ),
        sa.PrimaryKeyConstraint(
            "upload_id",
            "turn_idx",
            name=op.f("pk_review_turns"),
        ),
    )
    op.create_index(
        op.f("ix_review_turns_upload_id"),
        "review_turns",
        ["upload_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_review_turns_upload_id"), table_name="review_turns")
    op.drop_table("review_turns")
    op.drop_index(op.f("ix_review_uploads_user_id"), table_name="review_uploads")
    op.drop_table("review_uploads")
