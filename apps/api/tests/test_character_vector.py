"""Tests for `CharacterVector` (Character Engine L1).

Two halves:
  * dataclass-level invariants — validation, immutability, round-trip;
  * catalog-level invariants — every seeded ScenarioRecord has a
    well-formed vector, the demo trio (sc_001/002/003) has been
    explicitly backfilled, and the rest fall back to neutral until
    L1.2 fills them in.
"""

from __future__ import annotations

import dataclasses

import pytest
from app.services.scenarios.character_vector import (
    VECTOR_DIMENSIONS,
    VECTOR_MAX,
    VECTOR_MIN,
    VECTOR_NEUTRAL,
    CharacterVector,
)
from app.services.scenarios.seed_data import FALLBACK_RECORD, SCENARIO_CATALOG


def test_neutral_is_fifty_across_all_dimensions() -> None:
    vector = CharacterVector.neutral()

    for name in VECTOR_DIMENSIONS:
        assert getattr(vector, name) == VECTOR_NEUTRAL


def test_dimensions_tuple_is_canonical_six_in_order() -> None:
    """Prompt builder + DB column order pin this — re-ordering or
    renaming a dim is a breaking change and must surface in a diff."""
    assert VECTOR_DIMENSIONS == (
        "aggression",
        "empathy",
        "control",
        "honesty",
        "stability",
        "power_gap",
    )


def test_construction_with_explicit_values() -> None:
    vector = CharacterVector(
        aggression=80,
        empathy=20,
        control=70,
        honesty=40,
        stability=90,
        power_gap=85,
    )

    assert vector.aggression == 80
    assert vector.empathy == 20
    assert vector.power_gap == 85


@pytest.mark.parametrize("value", [-1, 101, 200, -50])
def test_out_of_range_raises_value_error(value: int) -> None:
    with pytest.raises(ValueError, match="out of range"):
        CharacterVector(
            aggression=value,
            empathy=50,
            control=50,
            honesty=50,
            stability=50,
            power_gap=50,
        )


@pytest.mark.parametrize("value", [50.5, "50", None, True])
def test_non_int_raises_type_error(value: object) -> None:
    with pytest.raises(TypeError, match="must be int"):
        CharacterVector(
            aggression=value,  # type: ignore[arg-type]
            empathy=50,
            control=50,
            honesty=50,
            stability=50,
            power_gap=50,
        )


def test_boundary_values_zero_and_hundred_accepted() -> None:
    """A pure pacifist (aggression=0) or a tyrant (control=100) must
    construct without complaint — the 0-100 range is inclusive."""
    CharacterVector(
        aggression=VECTOR_MIN,
        empathy=VECTOR_MAX,
        control=VECTOR_MAX,
        honesty=VECTOR_MIN,
        stability=VECTOR_NEUTRAL,
        power_gap=VECTOR_MIN,
    )


def test_frozen_dataclass_cannot_mutate() -> None:
    vector = CharacterVector.neutral()

    with pytest.raises(dataclasses.FrozenInstanceError):
        vector.aggression = 90  # type: ignore[misc]


def test_to_dict_round_trips_via_from_dict() -> None:
    original = CharacterVector(
        aggression=60,
        empathy=30,
        control=75,
        honesty=50,
        stability=80,
        power_gap=70,
    )

    rebuilt = CharacterVector.from_dict(original.to_dict())

    assert rebuilt == original


def test_from_dict_fills_missing_dims_with_neutral() -> None:
    """A partial-backfill row in the DB should still parse — the L1.2
    PR backfills lazily, so some rows may have only a couple of dims
    while the rest remain at the JSON server-default. Anything missing
    here means the prompt builder reads 50 for that dim, which matches
    the SQL server_default."""
    partial = {"aggression": 90}

    vector = CharacterVector.from_dict(partial)

    assert vector.aggression == 90
    assert vector.empathy == VECTOR_NEUTRAL
    assert vector.power_gap == VECTOR_NEUTRAL


def test_from_dict_ignores_unknown_keys() -> None:
    """Forward compatibility — a future 7th dim must not break parsing
    of older code paths that don't know about it."""
    payload = {
        "aggression": 70,
        "empathy": 30,
        "control": 50,
        "honesty": 50,
        "stability": 50,
        "power_gap": 50,
        "future_dim_charisma": 80,
    }

    vector = CharacterVector.from_dict(payload)

    assert vector.aggression == 70


def test_to_dict_has_exactly_six_canonical_keys() -> None:
    keys = set(CharacterVector.neutral().to_dict().keys())

    assert keys == set(VECTOR_DIMENSIONS)


# --- Catalog-level invariants -------------------------------------------------


def test_every_catalog_record_has_a_well_formed_vector() -> None:
    """Whether explicitly backfilled or defaulted to neutral, every row
    must hand the prompt builder a valid 6-dim vector — the prompt
    builder treats `character_vector` as non-null."""
    for record in SCENARIO_CATALOG:
        vector = record.character_vector

        assert isinstance(vector, CharacterVector), record.id
        for name in VECTOR_DIMENSIONS:
            value = getattr(vector, name)
            assert VECTOR_MIN <= value <= VECTOR_MAX, f"{record.id}.{name}={value} out of range"


def test_demo_trio_has_been_explicitly_backfilled() -> None:
    """sc_001 / sc_002 / sc_003 are the PRD §1.3 dogfood trio. They
    must carry real curated vectors, not the neutral baseline — the
    demo path is what evaluators see first."""
    by_id = {record.id: record for record in SCENARIO_CATALOG}
    neutral = CharacterVector.neutral()

    for scenario_id in ("sc_001", "sc_002", "sc_003"):
        record = by_id[scenario_id]
        assert record.character_vector != neutral, (
            f"{scenario_id} still has the neutral default — L1.1 must backfill this row"
        )


def test_demo_trio_vectors_match_persona_intent() -> None:
    """Specific spot-checks so the next PR adjusting these doesn't
    silently flatten the persona contrast that L1.3's prompt builder
    will rely on.

    sc_001 强硬型 HR — boss role, high power_gap + control.
    sc_002 老 HR     — political, low honesty + high stability.
    sc_003 同寝室友  — peer, near-zero power_gap.
    """
    by_id = {record.id: record for record in SCENARIO_CATALOG}

    sc001 = by_id["sc_001"].character_vector
    assert sc001.power_gap >= 60, sc001
    assert sc001.control >= 60, sc001

    sc002 = by_id["sc_002"].character_vector
    assert sc002.honesty <= 40, sc002
    assert sc002.stability >= 70, sc002

    sc003 = by_id["sc_003"].character_vector
    assert sc003.power_gap <= 20, sc003


def test_fallback_record_carries_neutral_vector() -> None:
    """When a typo / unknown scenario_id falls back, the prompt builder
    still needs a vector — neutral is the safe choice (no opinionated
    persona to model)."""
    assert FALLBACK_RECORD.character_vector == CharacterVector.neutral()
