"""scenario certification count + student ids

Replaces the coarse `real_user_certified` boolean (PRD §3.0.5 D) with
the structured form the content-ops team needs to track the ≥ 5-student
certification gate: a count plus the anonymised student-id list that
backs the count up.

`certification_count` is the canonical gate value (`>= 5` ⇔ certified);
`certified_student_ids` carries the anonymised IDs of the students who
validated the scenario. The two diverge only while content-ops is
mid-backfill — a CI-guarded invariant in `test_scenarios_repository`
asserts `len(ids) >= count` so a stray count bump can't slip past the
≥ 5 gate without traceability.

The two scenarios that were already marked True (sc_001 周末加班谈判
and sc_002 实习转正薪资谈判, PRD §1.3 dogfood pair) get count = 5 with
five placeholder IDs each; the §3.0.5 D content-ops follow-up swaps
those placeholders for real student IDs.

Revision ID: b5f2a9c4d18a
Revises: e7c2a5d09b16
Create Date: 2026-05-23 12:00:00.000000+00:00

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "b5f2a9c4d18a"
down_revision: str | None = "e7c2a5d09b16"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


# Five placeholder IDs per pre-certified scenario. Pattern
# `s_placeholder_<scenario>_<n>` makes a grep audit during the
# content-ops backfill trivial.
_PLACEHOLDER_IDS_SC001 = (
    '["s_placeholder_sc001_01","s_placeholder_sc001_02",'
    '"s_placeholder_sc001_03","s_placeholder_sc001_04",'
    '"s_placeholder_sc001_05"]'
)
_PLACEHOLDER_IDS_SC002 = (
    '["s_placeholder_sc002_01","s_placeholder_sc002_02",'
    '"s_placeholder_sc002_03","s_placeholder_sc002_04",'
    '"s_placeholder_sc002_05"]'
)


def upgrade() -> None:
    # Add new columns with safe defaults so existing rows materialise
    # as "not yet certified". JSON default uses ::json so PG round-trips
    # the literal as JSONB-compatible rather than text.
    op.add_column(
        "scenarios",
        sa.Column(
            "certification_count",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("0"),
        ),
    )
    op.add_column(
        "scenarios",
        sa.Column(
            "certified_student_ids",
            sa.JSON(),
            nullable=False,
            server_default=sa.text("'[]'::json"),
        ),
    )

    # Backfill the two pre-certified scenarios. The general-case backfill
    # (any other row with `real_user_certified = true`) bumps count to 5
    # with empty ids — the CI invariant catches that gap immediately, so
    # a future seed change can't silently inherit unbacked certifications.
    #
    # `_PLACEHOLDER_IDS_SC00X` are module-level constants generated from
    # a fixed pattern (no external input touches them), so the
    # f-string interpolation is safe — noqa: S608.
    # `_PLACEHOLDER_IDS_SC00X` are fixed module-level JSON literals — no
    # external input touches the SQL, so S608 is a false positive here.
    sql_sc001 = (
        "UPDATE scenarios SET certification_count = 5, "  # noqa: S608
        f"certified_student_ids = '{_PLACEHOLDER_IDS_SC001}'::json "
        "WHERE id = 'sc_001'"
    )
    sql_sc002 = (
        "UPDATE scenarios SET certification_count = 5, "  # noqa: S608
        f"certified_student_ids = '{_PLACEHOLDER_IDS_SC002}'::json "
        "WHERE id = 'sc_002'"
    )
    op.execute(sa.text(sql_sc001))
    op.execute(sa.text(sql_sc002))
    op.execute(
        sa.text(
            "UPDATE scenarios SET certification_count = 5 "
            "WHERE real_user_certified = true "
            "AND id NOT IN ('sc_001', 'sc_002')"
        )
    )

    op.drop_column("scenarios", "real_user_certified")


def downgrade() -> None:
    op.add_column(
        "scenarios",
        sa.Column(
            "real_user_certified",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
    )
    op.execute(
        sa.text("UPDATE scenarios SET real_user_certified = true WHERE certification_count >= 5")
    )
    op.drop_column("scenarios", "certified_student_ids")
    op.drop_column("scenarios", "certification_count")
