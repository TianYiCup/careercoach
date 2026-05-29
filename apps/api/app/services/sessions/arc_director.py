"""Arc director — Character Engine L2.

Watches where the conversation sits in a dramatic arc and tells the
MoodArbiter whether the opponent should build, press, react to a shift,
or wind down. Without it a 30-turn session is a flat wall of conflict;
with it the opponent has a *shape* — opening tension, escalation, a
turning beat when the user lands a decisive move, then resolution.

Hybrid detection (per the L2 design choice):

* **Turn-count guards** are deterministic and free:
  - the first `_OPENING_TURNS` turns are always `opening` (establish
    the situation before going full-throttle);
  - the last `_CLOSING_TAIL` turns before the cap are always `closing`
    (a session approaching the turn limit must wind down, not escalate
    into a hard stop).
* **LLM calibration** runs only in the middle window, classifying the
  latest exchange as `conflict` / `turning` / `closing`. Turning ("用户
  这一句改变了局面") and early resolution are exactly the beats a
  turn-counter can't see.

PR-OPT2: the director no longer makes its own LLM call. At the edges it
resolves a stage deterministically; in the middle window it returns
`None` and the MoodArbiter classifies the stage *in the same LLM call*
that predicts the next mood (see `MoodArbiter.next_mood_with_stage`).
That folds two serial LLM calls into one, cutting per-turn latency. The
classification guide + parser live here so the arc owns the stage
vocabulary; the arbiter just borrows them for the merged prompt.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

ArcStage = Literal["opening", "conflict", "turning", "closing"]

# Deterministic guards. The first two turns establish the scene; the
# last two before the cap force a wind-down so a session that hits the
# turn limit resolves instead of being guillotined mid-escalation.
_OPENING_TURNS = 2
_CLOSING_TAIL = 2

# Per-stage directive injected into the MoodArbiter prompt. Phrased as
# stage-direction guidance, not hard mood numbers — the arbiter still
# owns the per-dim prediction; the arc just biases its direction.
STAGE_DIRECTIVES: dict[ArcStage, str] = {
    "opening": "现在是开场试探阶段：先立住人设和张力，别一上来就用尽全力，给对话留升级空间。",
    "conflict": "现在是冲突升级阶段：对手该加压、寸步不让，把矛盾顶到明面上。",
    "turning": (
        "刚刚出现转折——用户这一句改变了局面。对手要真实回应这个转变："
        "被说动就软化、被激怒就爆发，绝不能装作什么都没发生、继续原样推进。"
    ),
    "closing": (
        "现在是收尾阶段：无论之前多激烈，对手都该开始收束，往一个结局走"
        "（让步、妥协，或摊牌后冷却），不要再无限升级。"
    ),
}

# The three stages the middle window can resolve to. `opening` /
# `closing` are owned by the deterministic edges, never classified.
MIDDLE_STAGES: tuple[ArcStage, ...] = ("conflict", "turning", "closing")

# Reused verbatim inside the merged arbiter prompt (PR-OPT2) so the
# stage vocabulary has a single source of truth.
STAGE_CLASSIFICATION_GUIDE = (
    "- conflict：还在僵持/升级，谁也没让步\n"
    "- turning：用户这句话改变了局面（戳中要害、亮出筹码、对手被说动或被激怒）\n"
    "- closing：矛盾在收束，双方在走向某种结局（让步、妥协、或谈崩冷却）"
)


@dataclass(frozen=True)
class ArcState:
    """Resolved arc for one turn: the stage label + the directive string
    the MoodArbiter consumes. `directive` is always populated (the
    `STAGE_DIRECTIVES` lookup is total over `ArcStage`)."""

    stage: ArcStage
    directive: str


class ArcDirector:
    """Resolves the arc stage deterministically at the edges. In the
    middle window it returns `None`, deferring the classification to the
    MoodArbiter's merged call (PR-OPT2). No LLM dependency."""

    def resolve(self, *, turn_index: int, turns_left: int) -> ArcState | None:
        """Resolve the stage for the current turn, or `None` when it
        falls in the middle window (the caller then asks the arbiter to
        classify it alongside the mood prediction).

        `turn_index` is 1-based (the turn being processed). `turns_left`
        is how many turns remain after this one — used for the closing
        guard so the arc winds down near the cap."""
        if turn_index <= _OPENING_TURNS:
            return self._state("opening")
        if turns_left <= _CLOSING_TAIL:
            return self._state("closing")
        return None

    @staticmethod
    def _state(stage: ArcStage) -> ArcState:
        return ArcState(stage=stage, directive=STAGE_DIRECTIVES[stage])


def parse_stage(raw: str) -> ArcStage | None:
    """First matching middle-stage keyword wins. Tolerant of the model
    wrapping the word in punctuation / quotes."""
    text = raw.strip().lower()
    for stage in MIDDLE_STAGES:
        if stage in text:
            return stage
    return None


__all__ = [
    "MIDDLE_STAGES",
    "STAGE_CLASSIFICATION_GUIDE",
    "STAGE_DIRECTIVES",
    "ArcDirector",
    "ArcStage",
    "ArcState",
    "parse_stage",
]
