"""create sharecards table

Revision ID: 9d4a1b7c2e08
Revises: 4b2a8e9f6c01
Create Date: 2026-05-12 22:00:00.000000+00:00

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "9d4a1b7c2e08"
down_revision: str | None = "4b2a8e9f6c01"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "sharecards",
        sa.Column("card_id", sa.String(length=32), nullable=False),
        sa.Column("card_type", sa.String(length=16), nullable=False),
        sa.Column("user_id", sa.String(length=64), nullable=False),
        sa.Column("session_id", sa.String(length=64), nullable=True),
        sa.Column("storage_key", sa.String(length=255), nullable=False),
        sa.Column("storage_backend", sa.String(length=32), nullable=False),
        sa.Column("user_caption", sa.String(length=120), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("card_id", name=op.f("pk_sharecards")),
    )
    op.create_index(
        op.f("ix_sharecards_card_type"),
        "sharecards",
        ["card_type"],
        unique=False,
    )
    op.create_index(
        op.f("ix_sharecards_user_id"),
        "sharecards",
        ["user_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_sharecards_session_id"),
        "sharecards",
        ["session_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_sharecards_session_id"), table_name="sharecards")
    op.drop_index(op.f("ix_sharecards_user_id"), table_name="sharecards")
    op.drop_index(op.f("ix_sharecards_card_type"), table_name="sharecards")
    op.drop_table("sharecards")
