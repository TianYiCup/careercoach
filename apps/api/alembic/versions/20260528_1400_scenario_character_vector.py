"""scenario character_vector (Character Engine L1)

Adds the 6-dim persona profile column to the scenarios table. v1 reads
the catalog from `app.services.scenarios.seed_data` (in-Python), so this
migration is forward-only schema prep — when the DB-backed catalog lands
a follow-up will replay the seed module to populate rows.

Dimensions (each 0-100): aggression / empathy / control / honesty /
stability / power_gap. The neutral baseline of 50 across all six is the
server default so the column is non-nullable from day one and the prompt
builder never has to handle a missing vector.

Revision ID: c8d3e1a09f47
Revises: b5f2a9c4d18a
Create Date: 2026-05-28 14:00:00.000000+00:00

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "c8d3e1a09f47"
down_revision: str | None = "b5f2a9c4d18a"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


# JSON literal for the neutral baseline. Kept verbatim instead of
# serialised from `CharacterVector.neutral().to_dict()` so the migration
# is reproducible from the file alone — alembic shouldn't import live
# app code that could shift under a future refactor.
#
# Each `:` is escaped as `\:` because `sa.text()` treats unescaped `:`
# followed by alphanumerics as a bindparam placeholder — without the
# escape, `"aggression":50` renders as `"aggression"NULL` and Postgres
# rejects the resulting JSON literal at migration time.
_NEUTRAL_VECTOR_JSON = (
    r'{"aggression"\:50,"empathy"\:50,"control"\:50,'
    r'"honesty"\:50,"stability"\:50,"power_gap"\:50}'
)


def upgrade() -> None:
    op.add_column(
        "scenarios",
        sa.Column(
            "character_vector",
            sa.JSON(),
            nullable=False,
            server_default=sa.text(f"'{_NEUTRAL_VECTOR_JSON}'::json"),
        ),
    )


def downgrade() -> None:
    op.drop_column("scenarios", "character_vector")
