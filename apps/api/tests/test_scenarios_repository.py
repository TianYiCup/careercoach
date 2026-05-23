"""Tests for `InMemoryScenarioRepository`.

These pin the catalog shape: every seeded scenario must be retrievable
by id, the listing must round-trip immutably, and unknown ids must
fail closed (None) so the service layer can decide whether to 404.
"""

from __future__ import annotations

from collections import Counter

from app.services.scenarios.repository import InMemoryScenarioRepository
from app.services.scenarios.seed_data import (
    REAL_USER_CERTIFIED_MIN_COUNT,
    SCENARIO_CATALOG,
)


async def test_list_all_returns_every_seeded_scenario() -> None:
    repo = InMemoryScenarioRepository()

    records = await repo.list_all()

    assert len(records) == len(SCENARIO_CATALOG)
    ids = {r.id for r in records}
    assert {"sc_001", "sc_002", "sc_003"} <= ids  # MSW-compatible core


async def test_list_all_returns_fresh_list_callers_can_mutate() -> None:
    """Callers must not be able to reach into the singleton's view.

    The protocol returns a list (not a tuple) precisely so the route
    layer can sort or slice without freezing the rest of the system."""
    repo = InMemoryScenarioRepository()

    first = await repo.list_all()
    first.clear()

    second = await repo.list_all()
    assert second  # still populated after the caller emptied their copy


async def test_get_known_id_returns_record() -> None:
    repo = InMemoryScenarioRepository()

    record = await repo.get("sc_001")

    assert record is not None
    assert record.title == "周末加班谈判"
    assert record.persona_title == "强硬型 HR"


async def test_get_unknown_id_returns_none() -> None:
    """Repository fails closed on unknown ids — service decides the
    HTTP response. The session-side seed lookup uses its own
    `FALLBACK_RECORD` for the demo "any string works" flow."""
    repo = InMemoryScenarioRepository()

    assert await repo.get("sc_does_not_exist") is None


async def test_catalog_spans_all_four_categories() -> None:
    """Filter coverage relies on at least one scenario per category."""
    repo = InMemoryScenarioRepository()
    records = await repo.list_all()

    categories = {r.category for r in records}
    assert categories == {"campus", "jobhunt", "intern", "life"}


def test_catalog_meets_prd_us_a1_minimums() -> None:
    """PRD US-A1 L2: ≥ 40 scenarios with per-category lower bounds
    (campus 12 / jobhunt 10 / intern 10 / life 8). Guards against a
    future PR shrinking the catalog below the answer-day spec — §10.2
    needs ≥ 30, §US-A1 wants ≥ 40."""
    counts = Counter(r.category for r in SCENARIO_CATALOG)
    assert len(SCENARIO_CATALOG) >= 40, f"catalog has {len(SCENARIO_CATALOG)}, need 40+"
    assert counts["campus"] >= 12, counts
    assert counts["jobhunt"] >= 10, counts
    assert counts["intern"] >= 10, counts
    assert counts["life"] >= 8, counts


def test_catalog_ids_unique_and_records_well_formed() -> None:
    """A duplicate id would let one scenario silently shadow another in
    `_BY_ID`; difficulty must stay in the 1-5 band the picker renders."""
    ids = [r.id for r in SCENARIO_CATALOG]
    assert len(ids) == len(set(ids)), "duplicate scenario id in the catalog"
    for record in SCENARIO_CATALOG:
        assert record.id.startswith("sc_"), record.id
        assert 1 <= record.difficulty <= 5, f"{record.id}: difficulty {record.difficulty}"
        assert record.category in {"campus", "jobhunt", "intern", "life"}, record.id
        assert record.title and record.background and record.opening_line, record.id


def test_certification_count_matches_certified_student_ids() -> None:
    """PRD §3.0.5 D — a scenario's certification count must be backed by
    at least that many anonymised student IDs.

    This is the CI gate: a future seed change can't bump
    `certification_count` past `REAL_USER_CERTIFIED_MIN_COUNT` without
    also adding the validators' IDs. Both numbers can never be negative;
    the count can exceed `len(ids)` only during a partial-trace backfill
    where content-ops added a student but not yet their ID — which is
    the failure mode this assertion is here to catch.
    """
    failures: list[str] = []
    for record in SCENARIO_CATALOG:
        if record.certification_count < 0:
            failures.append(f"{record.id}: negative count {record.certification_count}")
            continue
        if len(record.certified_student_ids) < record.certification_count:
            failures.append(
                f"{record.id}: count={record.certification_count} > "
                f"ids={len(record.certified_student_ids)} — backfill broken"
            )
    assert not failures, "\n".join(failures)


def test_certified_scenarios_meet_min_student_threshold() -> None:
    """`is_certified` must imply `certification_count >= 5`.

    Read-only derived check: catches a future refactor that lets
    `is_certified` return True for an uncertified row (e.g. an
    accidental `> 0` instead of `>= 5`)."""
    for record in SCENARIO_CATALOG:
        if record.is_certified:
            assert record.certification_count >= REAL_USER_CERTIFIED_MIN_COUNT, (
                f"{record.id}: marked certified but count "
                f"{record.certification_count} < {REAL_USER_CERTIFIED_MIN_COUNT}"
            )


def test_certified_student_ids_unique_within_scenario() -> None:
    """Duplicates would inflate the apparent certifier pool — the same
    student counted twice is one validator, not two."""
    for record in SCENARIO_CATALOG:
        ids = record.certified_student_ids
        assert len(ids) == len(set(ids)), (
            f"{record.id}: duplicate IDs in certified_student_ids={ids}"
        )
