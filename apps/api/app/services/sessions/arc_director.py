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
  turn-counter can't see. The call is tiny (one-word answer) and falls
  back to `conflict` on any failure, so the arc never blocks the turn.

The resolved stage + its directive is injected into the MoodArbiter
prompt (L3), so e.g. a `closing` directive pulls even a hostile boss
toward de-escalation. The arc does NOT call the roleplay LLM directly —
it shapes mood, and mood shapes the reply.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Literal

import structlog

from app.llm import LLMProvider, Message

logger = structlog.get_logger(__name__)

ArcStage = Literal["opening", "conflict", "turning", "closing"]

# Deterministic guards. The first two turns establish the scene; the
# last two before the cap force a wind-down so a session that hits the
# turn limit resolves instead of being guillotined mid-escalation.
_OPENING_TURNS = 2
_CLOSING_TAIL = 2

# 3s — arc calibration is one of several LLM calls in the turn and runs
# before the user sees anything, so bail fast to the deterministic
# baseline rather than make them wait.
_ARC_TIMEOUT_SEC = 3.0

# Per-stage directive injected into the MoodArbiter prompt. Phrased as
# stage-direction guidance, not hard mood numbers — the arbiter still
# owns the per-dim prediction; the arc just biases its direction.
_DIRECTIVES: dict[ArcStage, str] = {
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

_MIDDLE_STAGES: tuple[ArcStage, ...] = ("conflict", "turning", "closing")

_CALIBRATION_PROMPT = (
    "你是对话节奏导演。下面是一段练习对话里用户和对手的最新一轮交锋。"
    "判断这一轮处在戏剧节奏的哪个阶段，只回一个词：\n"
    "- conflict：还在僵持/升级，谁也没让步\n"
    "- turning：用户这句话改变了局面（戳中要害、亮出筹码、对手被说动或被激怒）\n"
    "- closing：矛盾在收束，双方在走向某种结局（让步、妥协、或谈崩冷却）\n"
    "只输出 conflict / turning / closing 三者之一，不要解释、不要标点。"
)


@dataclass(frozen=True)
class ArcState:
    """Resolved arc for one turn: the stage label + the directive string
    the MoodArbiter consumes. `directive` is always populated (the
    `_DIRECTIVES` lookup is total over `ArcStage`)."""

    stage: ArcStage
    directive: str


class ArcDirector:
    """Resolves the arc stage for a turn. LLM-backed in the middle
    window, deterministic at the edges."""

    def __init__(self, llm: LLMProvider) -> None:
        self._llm = llm

    async def resolve(
        self,
        *,
        turn_index: int,
        turns_left: int,
        user_content: str,
        opponent_last_reply: str | None,
        trace_id: str,
        session_id: str,
    ) -> ArcState:
        """Resolve the stage for the current turn.

        `turn_index` is 1-based (the turn being processed). `turns_left`
        is how many turns remain after this one — used for the closing
        guard so the arc winds down near the cap."""
        # Deterministic edges.
        if turn_index <= _OPENING_TURNS:
            return self._state("opening")
        if turns_left <= _CLOSING_TAIL:
            return self._state("closing")

        # Middle window — ask the LLM to classify the beat.
        stage = await self._calibrate(
            user_content=user_content,
            opponent_last_reply=opponent_last_reply,
            trace_id=trace_id,
            session_id=session_id,
        )
        return self._state(stage)

    async def _calibrate(
        self,
        *,
        user_content: str,
        opponent_last_reply: str | None,
        trace_id: str,
        session_id: str,
    ) -> ArcStage:
        """One short LLM call → a middle-window stage. Falls back to
        `conflict` (the safe "keep pressing" default) on any failure so
        the turn never blocks on the arc."""
        opponent_line = opponent_last_reply or "（对手刚说了开场白）"
        user_prompt = f"对手上句：{opponent_line}\n用户这句：{user_content}\n这一轮是哪个阶段？"
        messages = [Message.system(_CALIBRATION_PROMPT), Message.user(user_prompt)]

        try:
            raw = await _collect(self._llm.stream_chat(messages, timeout=_ARC_TIMEOUT_SEC))
        except Exception as exc:  # arc never crashes the turn
            logger.warning(
                "arc_director_llm_failed",
                session_id=session_id,
                trace_id=trace_id,
                error=str(exc),
            )
            return "conflict"

        stage = _parse_stage(raw)
        if stage is None:
            logger.warning(
                "arc_director_unparseable",
                session_id=session_id,
                trace_id=trace_id,
                raw=raw[:80],
            )
            return "conflict"
        logger.info(
            "arc_director_stage",
            session_id=session_id,
            trace_id=trace_id,
            stage=stage,
        )
        return stage

    @staticmethod
    def _state(stage: ArcStage) -> ArcState:
        return ArcState(stage=stage, directive=_DIRECTIVES[stage])


def _parse_stage(raw: str) -> ArcStage | None:
    """First matching middle-stage keyword wins. Tolerant of the model
    wrapping the word in punctuation / quotes."""
    text = raw.strip().lower()
    for stage in _MIDDLE_STAGES:
        if stage in text:
            return stage
    return None


async def _collect(stream: AsyncIterator[str]) -> str:
    parts: list[str] = []
    async for chunk in stream:
        parts.append(chunk)
    return "".join(parts)


__all__ = ["ArcDirector", "ArcStage", "ArcState"]
