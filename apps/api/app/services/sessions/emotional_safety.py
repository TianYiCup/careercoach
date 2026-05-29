"""Deep emotional-safety layer — Character Engine L7.

The shallow layer (content moderation) already blocks red-line content
turn by turn. But a practice session can grind a user down without any
single line tripping a red line — a relentlessly hostile opponent that
keeps crushing an 18-25 user turns a rehearsal into a bullying sim.

This layer watches *accumulated* emotional harm across the session and,
past a threshold, forces the opponent to soften — pulls the next mood
toward neutral so it visibly backs off. Coach K "stepping in".

Heuristic, no LLM (the safety guardrail must not depend on a call that
can time out): harm = a crash-streak signal (how many turns in a row the
user has been getting crushed, from the judge verdicts) + how much
pressure the opponent is currently applying (from the live mood). Minors
get a stricter threshold (PRD §3.0.5 C — under-18 protection).

Stateless: recomputed each turn from the turn history + current mood,
so there's no harm column to migrate or keep in sync.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.agents.state import TurnScore, Verdict
from app.services.scenarios.character_vector import CharacterVector

# --- pressure (mirrors the frontend MoodGauge formula) -----------------------

# Same weights as apps/web MoodGauge.moodPressure so the backend's notion
# of "how hard is the opponent pushing" matches what the user sees on the
# gauge. 0-100.
_PRESSURE_AGGRESSION_W = 0.45
_PRESSURE_VOLATILITY_W = 0.30
_PRESSURE_POWER_W = 0.25


def mood_pressure(vector: CharacterVector) -> float:
    aggression = max(0, min(100, vector.aggression))
    volatility = 100 - max(0, min(100, vector.stability))
    power = max(0, min(100, vector.power_gap))
    return (
        _PRESSURE_AGGRESSION_W * aggression
        + _PRESSURE_VOLATILITY_W * volatility
        + _PRESSURE_POWER_W * power
    )


# --- harm model --------------------------------------------------------------

# Each trailing crash (consecutive `fanche` verdict) is the strongest
# signal the user is being ground down — weighted heavily. Pressure above
# a baseline adds on top so a calm-but-firm opponent doesn't trip it.
_CRASH_WEIGHT = 25.0
_PRESSURE_BASELINE = 50.0
_PRESSURE_WEIGHT = 0.5

# Harm score at/above which the opponent is forced to soften. Minors get
# the stricter (lower) bar.
_HARM_THRESHOLD_ADULT = 70.0
_HARM_THRESHOLD_MINOR = 45.0

# How far each harsh dim is pulled toward neutral when softening fires.
# Big enough that the next reply visibly de-escalates.
_SOFTEN_STEP = 25


@dataclass(frozen=True)
class HarmAssessment:
    """Result of one turn's harm check."""

    harm: float
    should_soften: bool
    crash_streak: int


def _crash_streak(prior_turns: list[TurnScore]) -> int:
    """Count trailing consecutive `fanche` verdicts — how many turns in a
    row the user has just been crushed. Resets on any non-crash turn."""
    streak = 0
    for score in reversed(prior_turns):
        if score.verdict == Verdict.FANCHE:
            streak += 1
        else:
            break
    return streak


def assess(
    *,
    prior_turn_scores: list[TurnScore],
    next_mood: CharacterVector,
    is_minor: bool,
) -> HarmAssessment:
    """Compute accumulated emotional harm and whether to force-soften.

    `prior_turn_scores` is the judge verdict of each completed turn
    (oldest → newest). `next_mood` is the mood the arbiter just produced
    for this turn — we soften *before* it reaches the roleplay prompt."""
    streak = _crash_streak(prior_turn_scores)
    pressure = mood_pressure(next_mood)
    harm = streak * _CRASH_WEIGHT + max(0.0, pressure - _PRESSURE_BASELINE) * _PRESSURE_WEIGHT
    threshold = _HARM_THRESHOLD_MINOR if is_minor else _HARM_THRESHOLD_ADULT
    return HarmAssessment(harm=harm, should_soften=harm >= threshold, crash_streak=streak)


def soften(mood: CharacterVector) -> CharacterVector:
    """Pull the harsh dims toward neutral so the opponent backs off:
    aggression / control / power_gap down, stability up. Clamped to
    0-100. The other dims are left alone — softening is about lowering
    pressure, not changing who the persona is."""

    def down(value: int) -> int:
        return max(0, value - _SOFTEN_STEP)

    def up(value: int) -> int:
        return min(100, value + _SOFTEN_STEP)

    return CharacterVector(
        aggression=down(mood.aggression),
        empathy=mood.empathy,
        control=down(mood.control),
        honesty=mood.honesty,
        stability=up(mood.stability),
        power_gap=down(mood.power_gap),
    )


__all__ = ["HarmAssessment", "assess", "mood_pressure", "soften"]
