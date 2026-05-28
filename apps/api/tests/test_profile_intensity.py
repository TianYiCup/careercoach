"""Unit tests for the L5 intensity scaling (pure functions)."""

from __future__ import annotations

from app.services.profile.intensity import experience_factor, scale_vector
from app.services.scenarios.character_vector import VECTOR_DIMENSIONS, CharacterVector

# 强硬型 HR, mirrors persona_vectors.sc_001.
HR = CharacterVector(
    aggression=60,
    empathy=30,
    control=75,
    honesty=50,
    stability=80,
    power_gap=70,
)


def test_experience_factor_floor_for_beginner() -> None:
    """0 observations → the softest factor (1 - max_soften)."""
    assert experience_factor(0) < 1.0


def test_experience_factor_full_for_veteran() -> None:
    assert experience_factor(20) == 1.0
    assert experience_factor(100) == 1.0


def test_experience_factor_is_monotonic() -> None:
    factors = [experience_factor(n) for n in range(0, 25, 5)]
    assert factors == sorted(factors)


def test_beginner_vector_pulled_toward_neutral() -> None:
    """A brand-new user (0 obs, no crutch) faces a softened opponent:
    every off-neutral dim moves closer to 50."""
    scaled = scale_vector(HR, total_observations=0, overrelied_strategy=None)

    # HR dims above 50 come down; below 50 come up — all toward 50.
    assert 50 < scaled.stability < HR.stability
    assert 50 < scaled.control < HR.control
    assert HR.empathy < scaled.empathy < 50


def test_veteran_vector_unchanged_without_crutch() -> None:
    """At full experience with no over-reliance, the vector is the raw
    catalog profile (no softening, no hardening)."""
    scaled = scale_vector(HR, total_observations=20, overrelied_strategy=None)

    assert scaled == HR


def test_overreliance_hardens_countering_dims() -> None:
    """A veteran who over-relies on 讨好 (placate) meets an opponent with
    higher power_gap + control and lower empathy — built to resist
    flattery."""
    base = scale_vector(HR, total_observations=20, overrelied_strategy=None)
    hardened = scale_vector(HR, total_observations=20, overrelied_strategy="placate")

    assert hardened.power_gap > base.power_gap
    assert hardened.control > base.control
    assert hardened.empathy < base.empathy


def test_scaled_values_stay_in_range() -> None:
    """Even an extreme base + hardening can't escape 0-100."""
    extreme = CharacterVector(
        aggression=95,
        empathy=5,
        control=95,
        honesty=5,
        stability=95,
        power_gap=95,
    )
    scaled = scale_vector(extreme, total_observations=20, overrelied_strategy="avoid")

    for name in VECTOR_DIMENSIONS:
        value = getattr(scaled, name)
        assert 0 <= value <= 100, f"{name}={value} out of range"


def test_unknown_overrelied_strategy_is_a_noop_hardening() -> None:
    """A strategy key with no counter mapping just applies experience
    scaling — no crash, no hardening."""
    base = scale_vector(HR, total_observations=20, overrelied_strategy=None)
    same = scale_vector(HR, total_observations=20, overrelied_strategy="not_a_strategy")

    assert same == base
