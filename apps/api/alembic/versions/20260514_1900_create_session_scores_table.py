"""create session_scores table

Revision ID: e92f7d4a8c61
Revises: c4f1a8e2b5d9
Create Date: 2026-05-14 19:00:00.000000+00:00

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "e92f7d4a8c61"
down_revision: str | None = "c4f1a8e2b5d9"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "session_scores",
        sa.Column("session_id", sa.String(length=32), nullable=False),
        sa.Column("scenario_title", sa.String(length=64), nullable=False),
        sa.Column("persona_title", sa.String(length=64), nullable=False),
        sa.Column("aura", sa.Integer(), nullable=False),
        sa.Column("logic", sa.Integer(), nullable=False),
        sa.Column("emotion", sa.Integer(), nullable=False),
        sa.Column("professionalism", sa.Integer(), nullable=False),
        sa.Column("goal_achieve", sa.Integer(), nullable=False),
        sa.Column("result", sa.String(length=16), nullable=False),
        sa.Column("highlights", sa.String(length=500), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["session_id"],
            ["sessions.session_id"],
            ondelete="CASCADE",
            name=op.f("fk_session_scores_session_id_sessions"),
        ),
        sa.PrimaryKeyConstraint("session_id", name=op.f("pk_session_scores")),
    )
    op.create_index(
        op.f("ix_session_scores_created_at"),
        "session_scores",
        ["created_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_session_scores_created_at"), table_name="session_scores")
    op.drop_table("session_scores")
