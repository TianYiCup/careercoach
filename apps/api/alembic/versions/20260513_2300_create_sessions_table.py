"""create sessions table

Revision ID: 7e3c4d5f1a92
Revises: 9d4a1b7c2e08
Create Date: 2026-05-13 23:00:00.000000+00:00

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "7e3c4d5f1a92"
down_revision: str | None = "9d4a1b7c2e08"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "sessions",
        sa.Column("session_id", sa.String(length=32), nullable=False),
        sa.Column("user_id", sa.String(length=64), nullable=False),
        sa.Column("mode", sa.String(length=16), nullable=False),
        sa.Column("scenario_id", sa.String(length=64), nullable=False),
        sa.Column("persona_id", sa.String(length=64), nullable=False),
        sa.Column("user_goal", sa.String(length=200), nullable=False),
        sa.Column(
            "status",
            sa.String(length=16),
            server_default="active",
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("ended_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("session_id", name=op.f("pk_sessions")),
    )
    op.create_index(
        op.f("ix_sessions_user_id"),
        "sessions",
        ["user_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_sessions_scenario_id"),
        "sessions",
        ["scenario_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_sessions_scenario_id"), table_name="sessions")
    op.drop_index(op.f("ix_sessions_user_id"), table_name="sessions")
    op.drop_table("sessions")
