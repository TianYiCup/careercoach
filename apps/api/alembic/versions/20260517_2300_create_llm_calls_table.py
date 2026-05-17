"""create llm_calls table

Revision ID: e8b1d4f602a3
Revises: a4c81e93b275
Create Date: 2026-05-17 23:00:00.000000+00:00

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "e8b1d4f602a3"
down_revision: str | None = "a4c81e93b275"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "llm_calls",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("trace_id", sa.String(length=64), nullable=False),
        sa.Column("user_id", sa.String(length=64), nullable=False),
        sa.Column("surface", sa.String(length=16), nullable=False),
        sa.Column("model", sa.String(length=64), nullable=False),
        sa.Column("prompt_tokens", sa.Integer(), nullable=False),
        sa.Column("completion_tokens", sa.Integer(), nullable=False),
        sa.Column("total_tokens", sa.Integer(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_llm_calls")),
    )
    op.create_index(
        op.f("ix_llm_calls_trace_id"),
        "llm_calls",
        ["trace_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_llm_calls_user_id"),
        "llm_calls",
        ["user_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_llm_calls_surface"),
        "llm_calls",
        ["surface"],
        unique=False,
    )
    op.create_index(
        op.f("ix_llm_calls_created_at"),
        "llm_calls",
        ["created_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_llm_calls_created_at"), table_name="llm_calls")
    op.drop_index(op.f("ix_llm_calls_surface"), table_name="llm_calls")
    op.drop_index(op.f("ix_llm_calls_user_id"), table_name="llm_calls")
    op.drop_index(op.f("ix_llm_calls_trace_id"), table_name="llm_calls")
    op.drop_table("llm_calls")
