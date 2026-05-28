"""create user_strategy_stats table (Character Engine L5)

Per-(user, strategy) usage + effectiveness tallies. Coach K's L8
strategy read accumulates into these rows; L5 derives the user's
over-relied-but-failing strategy from them to harden the opponent's
countering dimensions at session create.

`(user_id, strategy)` is unique so the per-turn increment upserts in
place. Mirrors the `weaknesses` table shape (plain-string user_id, no
FK, text strategy key).

Revision ID: e1a9c7d3b482
Revises: d4e7b2c5f193
Create Date: 2026-05-28 23:30:00.000000+00:00
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "e1a9c7d3b482"
down_revision: str | None = "d4e7b2c5f193"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "user_strategy_stats",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("user_id", sa.String(length=64), nullable=False),
        sa.Column("strategy", sa.String(length=32), nullable=False),
        sa.Column("count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("good", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("mixed", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("poor", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "last_seen",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", "strategy", name="uq_user_strategy_stats_user_strategy"),
    )
    op.create_index(
        "ix_user_strategy_stats_user_id",
        "user_strategy_stats",
        ["user_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_user_strategy_stats_user_id", table_name="user_strategy_stats")
    op.drop_table("user_strategy_stats")
