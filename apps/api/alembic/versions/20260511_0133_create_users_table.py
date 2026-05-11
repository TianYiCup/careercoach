"""create users table

Revision ID: c7310747b020
Revises:
Create Date: 2026-05-11 01:33:54.563360+00:00

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "c7310747b020"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

PERSONA_TYPE_ENUM = sa.Enum(
    "in_school",
    "intern",
    "graduate",
    name="persona_type",
    create_constraint=True,
)


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("phone", sa.String(length=20), nullable=False),
        sa.Column("nickname", sa.String(length=64), nullable=False),
        sa.Column("birthdate", sa.Date(), nullable=True),
        sa.Column(
            "is_minor",
            sa.Boolean(),
            server_default="false",
            nullable=False,
        ),
        sa.Column("persona_type", PERSONA_TYPE_ENUM, nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_users")),
        sa.UniqueConstraint("phone", name=op.f("uq_users_phone")),
    )


def downgrade() -> None:
    op.drop_table("users")
    # native_enum=True leaves the postgres TYPE behind; clean it up explicitly.
    PERSONA_TYPE_ENUM.drop(op.get_bind(), checkfirst=False)
