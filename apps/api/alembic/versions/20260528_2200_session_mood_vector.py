"""session mood_vector (Character Engine L3)

Adds the live 6-dim mood column to the sessions table. v1 reads / writes
from the in-Python repository at request time, so this migration is
forward-only schema prep — when the Postgres-backed repository becomes
the primary, the column is already there.

Seeded to the neutral baseline so an in-flight migration never lands a
NULL mood that the prompt builder can't parse. `SessionService.create_session`
overrides this with `scenario.character_vector` immediately, so the
neutral default only matters for migration-time rows.

Colons inside the JSON literal are escaped as `\\:` because `sa.text()`
treats `:name` as a bindparam placeholder — without the escape, `:50`
becomes NULL at DDL emit and Postgres rejects the literal. See the L1.1
migration `c8d3e1a09f47` for the same dance and the memory note.

Revision ID: d4e7b2c5f193
Revises: c8d3e1a09f47
Create Date: 2026-05-28 22:00:00.000000+00:00
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "d4e7b2c5f193"
down_revision: str | None = "c8d3e1a09f47"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


_NEUTRAL_MOOD_JSON = (
    r'{"aggression"\:50,"empathy"\:50,"control"\:50,'
    r'"honesty"\:50,"stability"\:50,"power_gap"\:50}'
)


def upgrade() -> None:
    op.add_column(
        "sessions",
        sa.Column(
            "mood_vector",
            sa.JSON(),
            nullable=False,
            server_default=sa.text(f"'{_NEUTRAL_MOOD_JSON}'::json"),
        ),
    )


def downgrade() -> None:
    op.drop_column("sessions", "mood_vector")
