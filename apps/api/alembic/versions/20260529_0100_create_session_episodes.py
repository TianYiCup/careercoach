"""create session_episodes table (Character Engine L6)

Per-(user, scenario) episodic memory: how the user's last practice of a
scenario went, so the opponent can recall it next time. `visit_count`
accumulates across sessions; `last_result` / `last_takeaway` hold the
most recent outcome.

`(user_id, scenario_id)` is unique so the session-end record upserts in
place. Mirrors the weaknesses / user_strategy_stats shape.

Revision ID: f2b8d4e60a17
Revises: e1a9c7d3b482
Create Date: 2026-05-29 01:00:00.000000+00:00
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "f2b8d4e60a17"
down_revision: str | None = "e1a9c7d3b482"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "session_episodes",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("user_id", sa.String(length=64), nullable=False),
        sa.Column("scenario_id", sa.String(length=64), nullable=False),
        sa.Column("visit_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("last_result", sa.String(length=16), nullable=False),
        sa.Column("last_takeaway", sa.String(length=300), nullable=False, server_default=""),
        sa.Column(
            "last_seen",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", "scenario_id", name="uq_session_episodes_user_scenario"),
    )
    op.create_index(
        "ix_session_episodes_user_id",
        "session_episodes",
        ["user_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_session_episodes_user_id", table_name="session_episodes")
    op.drop_table("session_episodes")
