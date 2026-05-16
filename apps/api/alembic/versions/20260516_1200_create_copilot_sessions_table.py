"""create copilot_sessions table

Revision ID: a4c81e93b275
Revises: f3a7c91d4b62
Create Date: 2026-05-16 12:00:00.000000+00:00

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "a4c81e93b275"
down_revision: str | None = "f3a7c91d4b62"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "copilot_sessions",
        sa.Column("copilot_id", sa.String(length=32), nullable=False),
        sa.Column("user_id", sa.String(length=64), nullable=False),
        sa.Column("scenario_hint", sa.String(length=200), nullable=False),
        sa.Column(
            "privacy_level",
            sa.String(length=16),
            server_default="standard",
            nullable=False,
        ),
        sa.Column(
            "status",
            sa.String(length=16),
            server_default="pending",
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("connected_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("ended_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("copilot_id", name=op.f("pk_copilot_sessions")),
    )
    op.create_index(
        op.f("ix_copilot_sessions_user_id"),
        "copilot_sessions",
        ["user_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_copilot_sessions_user_id"), table_name="copilot_sessions")
    op.drop_table("copilot_sessions")
