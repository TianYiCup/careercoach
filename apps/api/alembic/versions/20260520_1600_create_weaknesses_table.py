"""create weaknesses table

Revision ID: e7c2a5d09b16
Revises: d3a6b1c8f024
Create Date: 2026-05-20 16:00:00.000000+00:00

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "e7c2a5d09b16"
down_revision: str | None = "d3a6b1c8f024"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "weaknesses",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("user_id", sa.String(length=64), nullable=False),
        sa.Column("tag", sa.String(length=64), nullable=False),
        sa.Column("frequency", sa.Integer(), server_default="0", nullable=False),
        sa.Column(
            "last_seen",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_weaknesses")),
        sa.UniqueConstraint("user_id", "tag", name="uq_weaknesses_user_tag"),
    )
    # PRD §6.3: `Weakness(user_id, frequency DESC)` powers the profile
    # read. A plain (user_id, frequency) btree serves the DESC sort too
    # (Postgres scans it backward) and covers user_id lookups via the
    # leftmost-prefix rule.
    op.create_index(
        op.f("ix_weaknesses_user_frequency"),
        "weaknesses",
        ["user_id", "frequency"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_weaknesses_user_frequency"), table_name="weaknesses")
    op.drop_table("weaknesses")
