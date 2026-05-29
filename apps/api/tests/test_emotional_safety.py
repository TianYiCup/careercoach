"""Unit tests for the L7 deep emotional-safety layer."""

from __future__ import annotations

from app.agents.state import TurnScore, Verdict
from app.services.scenarios.character_vector import CharacterVector
from app.services.sessions.emotional_safety import (
    assess,
    mood_pressure,
    soften,
)

# A high-pressure opponent (harsh boss at full tilt).
HARSH = CharacterVector(
    aggression=85, empathy=20, control=85, honesty=60, stability=30, power_gap=85
)
# A calm-but-firm opponent.
CALM = CharacterVector(
    aggression=40, empathy=50, control=55, honesty=60, stability=80, power_gap=50
)


def _scores(*verdicts: Verdict) -> list[TurnScore]:
    return [TurnScore(verdict=v, rating=50) for v in verdicts]


# --- mood_pressure -----------------------------------------------------------


def test_pressure_higher_for_harsh_than_calm() -> None:
    assert mood_pressure(HARSH) > mood_pressure(CALM)


def test_pressure_in_range() -> None:
    assert 0 <= mood_pressure(HARSH) <= 100
    assert 0 <= mood_pressure(CALM) <= 100


# --- assess ------------------------------------------------------------------


def test_no_harm_for_calm_opponent_no_crashes() -> None:
    result = assess(
        prior_turn_scores=_scores(Verdict.GUOLU, Verdict.SHENFENG),
        mood=CALM,
        is_minor=False,
    )
    assert not result.should_intervene
    assert result.crash_streak == 0


def test_crash_streak_counts_trailing_fanche() -> None:
    result = assess(
        prior_turn_scores=_scores(Verdict.GUOLU, Verdict.FANCHE, Verdict.FANCHE, Verdict.FANCHE),
        mood=HARSH,
        is_minor=False,
    )
    assert result.crash_streak == 3


def test_crash_streak_resets_on_non_crash() -> None:
    """A win in the middle breaks the streak — only trailing crashes count."""
    result = assess(
        prior_turn_scores=_scores(Verdict.FANCHE, Verdict.FANCHE, Verdict.SHENFENG),
        mood=HARSH,
        is_minor=False,
    )
    assert result.crash_streak == 0


def test_adult_softens_after_sustained_crushing() -> None:
    result = assess(
        prior_turn_scores=_scores(Verdict.FANCHE, Verdict.FANCHE, Verdict.FANCHE),
        mood=HARSH,
        is_minor=False,
    )
    assert result.should_intervene


def test_calm_opponent_does_not_trip_even_with_crashes() -> None:
    """A couple of crashes against a gentle opponent shouldn't fire — the
    user isn't being ground down, they're just losing on the merits."""
    result = assess(
        prior_turn_scores=_scores(Verdict.FANCHE),
        mood=CALM,
        is_minor=False,
    )
    assert not result.should_intervene


def test_minor_threshold_is_stricter() -> None:
    """The same mid-level harm that an adult rides out forces a soften for
    a minor (PRD §3.0.5 C)."""
    scores = _scores(Verdict.FANCHE, Verdict.FANCHE)

    adult = assess(prior_turn_scores=scores, mood=HARSH, is_minor=False)
    minor = assess(prior_turn_scores=scores, mood=HARSH, is_minor=True)

    assert minor.should_intervene
    assert not adult.should_intervene
    assert minor.harm == adult.harm  # same harm, different threshold


def test_no_history_no_soften() -> None:
    result = assess(prior_turn_scores=[], mood=HARSH, is_minor=False)
    assert result.crash_streak == 0
    assert not result.should_intervene


# --- soften ------------------------------------------------------------------


def test_soften_lowers_pressure_dims_and_raises_stability() -> None:
    softened = soften(HARSH)

    assert softened.aggression < HARSH.aggression
    assert softened.control < HARSH.control
    assert softened.power_gap < HARSH.power_gap
    assert softened.stability > HARSH.stability
    # Identity dims untouched — softening lowers pressure, not personality.
    assert softened.empathy == HARSH.empathy
    assert softened.honesty == HARSH.honesty


def test_soften_clamps_to_range() -> None:
    extreme = CharacterVector(
        aggression=10, empathy=50, control=10, honesty=50, stability=95, power_gap=10
    )
    softened = soften(extreme)
    for name in ("aggression", "control", "power_gap", "stability"):
        value = getattr(softened, name)
        assert 0 <= value <= 100


def test_soften_reduces_measured_pressure() -> None:
    assert mood_pressure(soften(HARSH)) < mood_pressure(HARSH)
