"""create turns table

Revision ID: c4f1a8e2b5d9
Revises: a8e1c5f3d042
Create Date: 2026-05-14 11:00:00.000000+00:00

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "c4f1a8e2b5d9"
down_revision: str | None = "a8e1c5f3d042"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "turns",
        sa.Column("turn_id", sa.String(length=16), nullable=False),
        sa.Column("session_id", sa.String(length=32), nullable=False),
        sa.Column("user_content", sa.String(length=2000), nullable=False),
        sa.Column("opponent_reply", sa.String(length=1000), nullable=False),
        sa.Column("coach_hint_safe", sa.String(length=200), nullable=False),
        sa.Column("coach_hint_aggressive", sa.String(length=200), nullable=False),
        sa.Column("coach_hint_humor", sa.String(length=200), nullable=False),
        sa.Column("verdict", sa.String(length=16), nullable=False),
        sa.Column("rating", sa.Integer(), nullable=False),
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
            name=op.f("fk_turns_session_id_sessions"),
        ),
        sa.PrimaryKeyConstraint("turn_id", name=op.f("pk_turns")),
    )
    op.create_index(
        op.f("ix_turns_session_id"),
        "turns",
        ["session_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_turns_session_id"), table_name="turns")
    op.drop_table("turns")
