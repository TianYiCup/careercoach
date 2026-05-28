"""Adaptive opponent intensity — Character Engine L5.

Turns the user's strategy profile into a scaled `CharacterVector` at
session create, so a beginner doesn't get a brick wall and a veteran
who keeps leaning on one failing tactic meets an opponent built to
punish it.

Two signals combine (the L5 design choice — "both"):

1. **Experience** (total strategy observations). A brand-new user gets
   the opponent's vector pulled toward neutral (softer); experience
   ramps it back to full by `_VETERAN_OBSERVATIONS`. This scales the
   whole vector's distance from 50.

2. **Over-reliance** — the strategy the user uses most while it keeps
   failing (poor-dominant). The opponent hardens the dimensions that
   counter that tactic, so the user is pushed to vary their game.

Both adjustments are clamped to [0, 100]. The function is pure — the
service owns the data fetch and hands the numbers in.
"""

from __future__ import annotations

from app.services.scenarios.character_vector import VECTOR_DIMENSIONS, CharacterVector

# Below this many observations the user is a "beginner" and gets the
# softest opponent; at/above `_VETERAN_OBSERVATIONS` they get the full
# catalog intensity. Linear ramp between.
_BEGINNER_OBSERVATIONS = 0
_VETERAN_OBSERVATIONS = 20

# How far toward neutral (50) a brand-new user's opponent is pulled.
# 0.35 keeps a first-timer's opponent noticeably gentler without
# watering it down — an 80-stability boss reads ~70, still firm.
_MAX_SOFTEN = 0.35

# A strategy is "over-relied + failing" once the user has leaned on it
# at least this many times with a majority of poor outcomes.
_OVERRELIANCE_MIN_COUNT = 3

# Per-strategy hardening: which opponent dims to push (and by how much)
# when the user over-relies on that strategy. Values are added before
# the [0,100] clamp. Keyed by the closed coach_strategy keys.
_COUNTER_DIMS: dict[str, dict[str, int]] = {
    # User keeps buttering up → opponent cares less about being liked,
    # holds more power, steers harder.
    "placate": {"empathy": -15, "power_gap": 15, "control": 15},
    # User keeps caving → opponent presses the advantage.
    "concede": {"aggression": 15, "control": 15},
    # User keeps dodging → opponent won't let them, stays on topic.
    "avoid": {"control": 20, "aggression": 10},
    "deflect": {"control": 20, "aggression": 10},
    # User leans on logic → opponent stonewalls with process, less candid.
    "reason": {"stability": 15, "honesty": -15},
    # Counter / direct are already strong plays — the opponent just gets
    # harder to rattle so the user can't coast on them.
    "counter": {"stability": 15},
    "direct": {"stability": 15},
}


def experience_factor(total_observations: int) -> float:
    """Linear ramp in [1 - _MAX_SOFTEN, 1] mapping observations → how
    much of the opponent's full intensity to apply. Beginner → softest,
    veteran → full (1.0)."""
    if total_observations >= _VETERAN_OBSERVATIONS:
        return 1.0
    span = _VETERAN_OBSERVATIONS - _BEGINNER_OBSERVATIONS
    progress = max(0, total_observations - _BEGINNER_OBSERVATIONS) / span
    return (1.0 - _MAX_SOFTEN) + _MAX_SOFTEN * progress


def scale_vector(
    base: CharacterVector,
    *,
    total_observations: int,
    overrelied_strategy: str | None,
) -> CharacterVector:
    """Apply experience softening + over-reliance hardening to `base`.

    Order matters: soften toward neutral first (experience), then add
    the counter-dim hardening, then clamp. So a beginner still gets a
    gentler opponent even if they over-rely on something, but the shape
    tilts toward punishing their crutch."""
    factor = experience_factor(total_observations)
    counters = _COUNTER_DIMS.get(overrelied_strategy or "", {})

    scaled: dict[str, int] = {}
    for name in VECTOR_DIMENSIONS:
        anchor = getattr(base, name)
        # Soften: pull `factor` of the way from neutral toward the anchor.
        softened = 50 + (anchor - 50) * factor
        hardened = softened + counters.get(name, 0)
        scaled[name] = max(0, min(100, round(hardened)))
    return CharacterVector.from_dict(scaled)


__all__ = ["experience_factor", "scale_vector"]
