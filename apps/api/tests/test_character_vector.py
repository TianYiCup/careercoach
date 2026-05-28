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
    describe_for_coach,
    describe_for_roleplay,
)
from app.services.scenarios.persona_vectors import PERSONA_VECTORS
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


def test_every_catalog_row_has_a_curated_vector_entry() -> None:
    """After L1.2, every row in SCENARIO_CATALOG must have an entry in
    PERSONA_VECTORS. A row that falls through to the neutral default
    means a content-ops gap — the prompt builder would render a
    "baseline" persona that doesn't match the picker copy."""
    missing = [record.id for record in SCENARIO_CATALOG if record.id not in PERSONA_VECTORS]

    assert not missing, f"catalog rows without PERSONA_VECTORS entry: {missing}"


def test_persona_vectors_covers_only_catalog_ids() -> None:
    """Inverse check — a vector with no matching catalog row is dead
    weight that drifts out of sync silently. Catch the mistake when
    someone deletes a scenario but forgets to remove its vector."""
    catalog_ids = {record.id for record in SCENARIO_CATALOG}
    orphaned = sorted(vector_id for vector_id in PERSONA_VECTORS if vector_id not in catalog_ids)

    assert not orphaned, f"PERSONA_VECTORS entries with no matching catalog row: {orphaned}"


def test_persona_vectors_stay_inside_15_to_85_band() -> None:
    """Curated vectors avoid the 0/100 extremes — those are reserved
    for later-epic archetype personas. Catching a stray boundary value
    here keeps the day-one catalog interpretable as "intensity dial
    within a normal range", not "absolute archetype"."""
    out_of_band: list[str] = []
    for scenario_id, vector in PERSONA_VECTORS.items():
        for name in VECTOR_DIMENSIONS:
            value = getattr(vector, name)
            if not 15 <= value <= 85:
                out_of_band.append(f"{scenario_id}.{name}={value}")

    assert not out_of_band, "values outside 15-85 band:\n" + "\n".join(out_of_band)


# --- L1.3 describe_for_roleplay ----------------------------------------------


def test_roleplay_descriptor_empty_for_neutral_vector() -> None:
    """The neutral fallback (every dim = 50) sits inside the silent
    31-69 band, so no bullets emit and the roleplay prompt should
    collapse to its pre-L1.3 shape."""
    assert describe_for_roleplay(CharacterVector.neutral()) == ""


def test_roleplay_descriptor_emits_high_phrases() -> None:
    """All-90 vector triggers the HIGH phrase for every dim. Pins the
    bullet count + the header so a regression that drops a dim is loud."""
    vector = CharacterVector(
        aggression=90,
        empathy=90,
        control=90,
        honesty=90,
        stability=90,
        power_gap=90,
    )

    descriptor = describe_for_roleplay(vector)

    assert descriptor.startswith("你的性格底色：\n")
    assert descriptor.count("\n- ") == 6  # one bullet per dim
    assert "说话带刺" in descriptor
    assert "是上位者" in descriptor


def test_roleplay_descriptor_emits_low_phrases() -> None:
    vector = CharacterVector(
        aggression=10,
        empathy=10,
        control=10,
        honesty=10,
        stability=10,
        power_gap=10,
    )

    descriptor = describe_for_roleplay(vector)

    assert "说话留余地" in descriptor
    assert "和用户对等" in descriptor
    assert "情绪不稳" in descriptor


@pytest.mark.parametrize(
    ("value", "should_emit"),
    [(0, True), (30, True), (31, False), (50, False), (69, False), (70, True), (100, True)],
)
def test_roleplay_descriptor_band_edges(value: int, should_emit: bool) -> None:
    """30 reads as LOW, 70 as HIGH (inclusive). 31-69 stays silent.
    Pinning the edges here catches a future re-tune of the threshold
    constants slipping through review."""
    vector = CharacterVector(
        aggression=value,
        empathy=50,
        control=50,
        honesty=50,
        stability=50,
        power_gap=50,
    )

    descriptor = describe_for_roleplay(vector)

    if should_emit:
        assert descriptor, f"expected a bullet for aggression={value}"
    else:
        assert descriptor == "", f"expected silence for aggression={value}, got: {descriptor!r}"


def test_roleplay_descriptor_bullet_order_matches_dimensions() -> None:
    """Deterministic order — re-tuning one persona shouldn't reshuffle
    the descriptor relative to another. Order follows VECTOR_DIMENSIONS."""
    vector = CharacterVector(
        aggression=10,  # LOW — first
        empathy=50,
        control=90,  # HIGH — second
        honesty=50,
        stability=10,  # LOW — third
        power_gap=90,  # HIGH — fourth
    )

    descriptor = describe_for_roleplay(vector)
    lines = [line for line in descriptor.splitlines() if line.startswith("- ")]

    # aggression LOW, control HIGH, stability LOW, power_gap HIGH in that order
    assert "留余地" in lines[0]
    assert "强势把话题" in lines[1]
    assert "情绪不稳" in lines[2]
    assert "上位者" in lines[3]


def test_roleplay_descriptor_for_sc_001_matches_persona_intent() -> None:
    """End-to-end: the demo trio's curated vectors should render the
    bullets a content reviewer would expect for 强硬型 HR — high
    control, high stability, high power_gap, and the low-empathy
    bullet for not caring you're tired."""
    sc_001 = PERSONA_VECTORS["sc_001"]

    descriptor = describe_for_roleplay(sc_001)

    assert "强势把话题" in descriptor
    assert "稳坐钓鱼台" in descriptor
    assert "是上位者" in descriptor
    assert "用户情绪迟钝" in descriptor


# --- L1.3 describe_for_coach -------------------------------------------------


def test_coach_descriptor_empty_for_neutral_vector() -> None:
    assert describe_for_coach(CharacterVector.neutral()) == ""


def test_coach_descriptor_focuses_on_three_tactical_dims() -> None:
    """Coach only cares about power_gap / stability / honesty — the
    other three are too situational for a single hint. An all-extreme
    vector should yield at most 3 bullets (power_gap, stability,
    honesty)."""
    vector = CharacterVector(
        aggression=90,  # not in coach view
        empathy=90,  # not in coach view
        control=90,  # not in coach view
        honesty=10,
        stability=90,
        power_gap=90,
    )

    descriptor = describe_for_coach(vector)

    assert descriptor.startswith("对手画像：\n")
    assert descriptor.count("\n- ") == 3


def test_coach_honesty_only_has_low_phrase() -> None:
    """High-honesty opponent doesn't need coach guidance — direct talk
    is already the easy case. The descriptor table reflects that
    asymmetry; an all-90 vector should NOT emit an honesty bullet."""
    high_honesty = CharacterVector(
        aggression=50,
        empathy=50,
        control=50,
        honesty=90,
        stability=50,
        power_gap=50,
    )

    descriptor = describe_for_coach(high_honesty)

    assert descriptor == ""


def test_coach_descriptor_for_sc_001_recommends_hierarchy_savvy_play() -> None:
    """强硬型 HR: high power_gap (don't硬顶) + high stability (利益层面更有效).
    Spot-checks the two phrases a reviewer would expect."""
    sc_001 = PERSONA_VECTORS["sc_001"]

    descriptor = describe_for_coach(sc_001)

    assert "上位者" in descriptor
    assert "情绪很稳" in descriptor
