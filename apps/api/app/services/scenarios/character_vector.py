"""Six-dimensional persona profile — Character Engine L1.

Replaces the single-label "强硬型 HR" persona with a 6-dim numeric
vector so the same identity (e.g. an HR) can present as either
hard-nosed or warm depending on the scenario, and the prompt builder
in `app.services.sessions.turn_service` can translate the values into
specific Chinese rhetorical hints rather than a one-size-fits-all
"adversarial" instruction.

Dimensions and their meaning at the extremes:

| Field      | 中文       | 0 端 (低)        | 100 端 (高)      |
|------------|-----------|------------------|------------------|
| aggression | 攻击性     | 温和、留余地      | 说话带刺、不绕弯子 |
| empathy    | 共情      | 不察觉对方感受    | 能精准命中你情绪   |
| control    | 控制欲     | 顺着用户走        | 强势主导节奏       |
| honesty    | 诚实       | 兜圈子 / 政治化   | 直球、不装         |
| stability  | 稳定       | 一推就炸         | 怎么撩都稳坐钓鱼台  |
| power_gap  | 权力差     | 平辈 / 弱势       | 上位者、可惩罚你   |

This module is intentionally pure-data — no LLM imports, no Pydantic
schema (the API exposure of vectors is a later PR). Both seed_data
and the custom-scenario generator depend on it.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final

VECTOR_DIMENSIONS: Final[tuple[str, ...]] = (
    "aggression",
    "empathy",
    "control",
    "honesty",
    "stability",
    "power_gap",
)
"""Canonical dimension order. The prompt builder iterates these by
name; tests pin the tuple so a re-ordering can't silently rewrite
prompts."""

VECTOR_MIN: Final[int] = 0
VECTOR_MAX: Final[int] = 100
VECTOR_NEUTRAL: Final[int] = 50


@dataclass(frozen=True)
class CharacterVector:
    """Six 0-100 ints describing one persona's interaction tendencies.

    Values cluster as: 0-20 dormant / 21-40 low / 41-60 baseline /
    61-80 strong / 81-100 dominant. The prompt builder consumes those
    bands rather than the raw integer so a small re-tuning of one
    scenario doesn't shift wording for every other one.
    """

    aggression: int
    empathy: int
    control: int
    honesty: int
    stability: int
    power_gap: int

    def __post_init__(self) -> None:
        for name in VECTOR_DIMENSIONS:
            value = getattr(self, name)
            if not isinstance(value, int) or isinstance(value, bool):
                raise TypeError(f"CharacterVector.{name} must be int, got {type(value).__name__}")
            if not VECTOR_MIN <= value <= VECTOR_MAX:
                raise ValueError(
                    f"CharacterVector.{name}={value} out of range [{VECTOR_MIN}, {VECTOR_MAX}]"
                )

    @classmethod
    def neutral(cls) -> CharacterVector:
        """All six dims = 50. Used for the fallback record and any
        un-backfilled seed entry so the prompt builder always has a
        complete vector to read."""
        return cls(
            aggression=VECTOR_NEUTRAL,
            empathy=VECTOR_NEUTRAL,
            control=VECTOR_NEUTRAL,
            honesty=VECTOR_NEUTRAL,
            stability=VECTOR_NEUTRAL,
            power_gap=VECTOR_NEUTRAL,
        )

    @classmethod
    def from_dict(cls, payload: dict[str, int]) -> CharacterVector:
        """Build from a JSON-decoded dict (DB / API path). Missing
        dimensions default to neutral so a partial backfill row still
        round-trips. Unknown keys are ignored — forward compat for a
        future 7th / 8th dim."""
        return cls(
            aggression=int(payload.get("aggression", VECTOR_NEUTRAL)),
            empathy=int(payload.get("empathy", VECTOR_NEUTRAL)),
            control=int(payload.get("control", VECTOR_NEUTRAL)),
            honesty=int(payload.get("honesty", VECTOR_NEUTRAL)),
            stability=int(payload.get("stability", VECTOR_NEUTRAL)),
            power_gap=int(payload.get("power_gap", VECTOR_NEUTRAL)),
        )

    def to_dict(self) -> dict[str, int]:
        """JSON-serialisable form for DB storage and API responses."""
        return {name: getattr(self, name) for name in VECTOR_DIMENSIONS}


__all__ = [
    "VECTOR_DIMENSIONS",
    "VECTOR_MAX",
    "VECTOR_MIN",
    "VECTOR_NEUTRAL",
    "CharacterVector",
]
