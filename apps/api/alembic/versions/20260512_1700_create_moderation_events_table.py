"""create moderation_events table

Revision ID: 4b2a8e9f6c01
Revises: c7310747b020
Create Date: 2026-05-12 17:00:00.000000+00:00

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "4b2a8e9f6c01"
down_revision: str | None = "c7310747b020"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "moderation_events",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("user_id", sa.String(length=64), nullable=False),
        sa.Column("session_id", sa.String(length=64), nullable=True),
        sa.Column("content_hash", sa.String(length=64), nullable=False),
        sa.Column("content_length", sa.Integer(), nullable=False),
        sa.Column("context", sa.String(length=32), nullable=False),
        sa.Column("verdict", sa.String(length=16), nullable=False),
        sa.Column("categories", sa.JSON(), nullable=False),
        sa.Column("score", sa.Float(), nullable=False),
        sa.Column("backend", sa.String(length=32), nullable=False),
        sa.Column("trace_id", sa.String(length=64), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_moderation_events")),
    )
    op.create_index(
        op.f("ix_moderation_events_user_id"),
        "moderation_events",
        ["user_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_moderation_events_session_id"),
        "moderation_events",
        ["session_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_moderation_events_content_hash"),
        "moderation_events",
        ["content_hash"],
        unique=False,
    )
    op.create_index(
        op.f("ix_moderation_events_trace_id"),
        "moderation_events",
        ["trace_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_moderation_events_trace_id"), table_name="moderation_events")
    op.drop_index(op.f("ix_moderation_events_content_hash"), table_name="moderation_events")
    op.drop_index(op.f("ix_moderation_events_session_id"), table_name="moderation_events")
    op.drop_index(op.f("ix_moderation_events_user_id"), table_name="moderation_events")
    op.drop_table("moderation_events")
