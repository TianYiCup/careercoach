"""create streaks table

Revision ID: d3a6b1c8f024
Revises: c2f5a9b3e017
Create Date: 2026-05-20 15:00:00.000000+00:00

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "d3a6b1c8f024"
down_revision: str | None = "c2f5a9b3e017"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "streaks",
        sa.Column("user_id", sa.String(length=64), nullable=False),
        sa.Column("current_days", sa.Integer(), server_default="0", nullable=False),
        sa.Column("max_days", sa.Integer(), server_default="0", nullable=False),
        sa.Column("last_active_date", sa.Date(), nullable=False),
        sa.PrimaryKeyConstraint("user_id", name=op.f("pk_streaks")),
    )


def downgrade() -> None:
    op.drop_table("streaks")
