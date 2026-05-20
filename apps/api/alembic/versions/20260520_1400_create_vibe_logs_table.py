"""create vibe_logs table

Revision ID: c2f5a9b3e017
Revises: e8b1d4f602a3
Create Date: 2026-05-20 14:00:00.000000+00:00

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "c2f5a9b3e017"
down_revision: str | None = "e8b1d4f602a3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "vibe_logs",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("user_id", sa.String(length=64), nullable=False),
        sa.Column("vibe", sa.String(length=16), nullable=False),
        sa.Column("logged_date", sa.Date(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_vibe_logs")),
        sa.UniqueConstraint("user_id", "logged_date", name="uq_vibe_logs_user_date"),
    )
    op.create_index(
        op.f("ix_vibe_logs_user_id"),
        "vibe_logs",
        ["user_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_vibe_logs_user_id"), table_name="vibe_logs")
    op.drop_table("vibe_logs")
