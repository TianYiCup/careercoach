"""create scenarios table

Revision ID: a8e1c5f3d042
Revises: 7e3c4d5f1a92
Create Date: 2026-05-14 09:30:00.000000+00:00

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "a8e1c5f3d042"
down_revision: str | None = "7e3c4d5f1a92"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "scenarios",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("title", sa.String(length=64), nullable=False),
        sa.Column("category", sa.String(length=16), nullable=False),
        sa.Column("difficulty", sa.Integer(), nullable=False),
        sa.Column("tags", sa.JSON(), nullable=False),
        sa.Column("background", sa.String(length=500), nullable=False),
        sa.Column(
            "real_user_certified",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=False,
        ),
        sa.Column("persona_title", sa.String(length=64), nullable=False),
        sa.Column("opening_line", sa.String(length=300), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_scenarios")),
    )
    op.create_index(
        op.f("ix_scenarios_category"),
        "scenarios",
        ["category"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_scenarios_category"), table_name="scenarios")
    op.drop_table("scenarios")
