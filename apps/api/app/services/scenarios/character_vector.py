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


# --- Vector → Chinese prompt descriptor (L1.3) -------------------------------

# A dimension only contributes a phrase if it sits in the outer 30 / 70
# bands — values inside 31-69 are "baseline" and stay silent so the
# descriptor reads as "what's distinctive about this persona" instead of
# describing every dim every time. Edges are inclusive: a 30 reads LOW,
# a 70 reads HIGH.
_LOW_BAND_MAX = 30
_HIGH_BAND_MIN = 70


# Per-dim Chinese phrasing at each extreme. Kept short so several
# bullets together still fit inside the roleplay prompt without
# blowing the model's instruction budget. Tone aligns with教练 K's
# "嘴硬心软不爹味" voice (PRD §3.0.5) — descriptive, not prescriptive.
_ROLEPLAY_DESCRIPTORS: dict[str, dict[str, str]] = {
    "aggression": {
        "low": "说话留余地，不顶撞，被刺也忍一下",
        "high": "说话带刺、不绕弯子，直接给压力",
    },
    "empathy": {
        "low": "对用户情绪迟钝，不察觉对方情绪上来",
        "high": "能精准察觉用户情绪，但未必愿意让步",
    },
    "control": {
        "low": "不主导节奏，用户抛什么接什么",
        "high": "强势把话题往自己想要的方向带",
    },
    "honesty": {
        "low": "兜圈子，用「流程」「规定」「预算」当挡箭牌，不正面回答",
        "high": "直球，有什么说什么，不装",
    },
    "stability": {
        "low": "情绪不稳，一推就炸 / 失态",
        "high": "无论用户怎么撩都稳坐钓鱼台，不轻易破防",
    },
    "power_gap": {
        "low": "和用户对等，没有谁能压谁",
        "high": "是上位者，可以决定用户的成败 / 待遇 / 评价",
    },
}


# Coach K reads a more compact view — it's the user's strategist, so
# only the dims that change tactical advice matter (how much hierarchy
# to navigate, how to land an emotional vs logical appeal, whether the
# opponent is being evasive).
_COACH_DESCRIPTORS: dict[str, dict[str, str]] = {
    "power_gap": {
        "low": "对方和用户对等，可以正面回应，不必小心翼翼",
        "high": "对方是上位者，硬顶代价高，建议先稳住关系再争取",
    },
    "stability": {
        "low": "对方情绪不稳，温和坚定 > 硬刚",
        "high": "对方情绪很稳，利益 / 逻辑层面比情绪施压更有效",
    },
    "honesty": {
        "low": "对方在兜圈子，可以直接戳破，要事实不要话术",
    },
}


def describe_for_roleplay(vector: CharacterVector) -> str:
    """Render the bullet block injected into the roleplay system prompt.

    Returns the empty string when every dim sits in the silent 31-69
    band (e.g. the neutral fallback record), so the existing prompt
    template still reads correctly without an empty header line. Order
    follows `VECTOR_DIMENSIONS` so the descriptor stays deterministic
    across re-tunes.
    """
    bullets: list[str] = []
    for name in VECTOR_DIMENSIONS:
        value = getattr(vector, name)
        phrase = _phrase_for(_ROLEPLAY_DESCRIPTORS, name, value)
        if phrase is not None:
            bullets.append(f"- {phrase}")
    if not bullets:
        return ""
    return "你的性格底色：\n" + "\n".join(bullets)


def describe_for_coach(vector: CharacterVector) -> str:
    """Compact opponent profile for coach K's hint prompt.

    Only emits hints for the three dims that change tactical advice
    (power_gap / stability / honesty). Honesty only has a LOW phrase
    on purpose — a high-honesty opponent is the easy case and doesn't
    need special coach guidance.
    """
    bullets: list[str] = []
    for name in ("power_gap", "stability", "honesty"):
        value = getattr(vector, name)
        phrase = _phrase_for(_COACH_DESCRIPTORS, name, value)
        if phrase is not None:
            bullets.append(f"- {phrase}")
    if not bullets:
        return ""
    return "对手画像：\n" + "\n".join(bullets)


def _phrase_for(table: dict[str, dict[str, str]], name: str, value: int) -> str | None:
    """Look up the LOW / HIGH phrase for `name` in `table`. Returns None
    when the value sits in the silent band or the table has no phrase
    for that side (e.g. coach honesty only has `low`)."""
    if value <= _LOW_BAND_MAX:
        return table.get(name, {}).get("low")
    if value >= _HIGH_BAND_MIN:
        return table.get(name, {}).get("high")
    return None


__all__ = [
    "VECTOR_DIMENSIONS",
    "VECTOR_MAX",
    "VECTOR_MIN",
    "VECTOR_NEUTRAL",
    "CharacterVector",
    "describe_for_coach",
    "describe_for_roleplay",
]
