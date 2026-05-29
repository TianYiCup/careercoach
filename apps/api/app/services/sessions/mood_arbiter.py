"""LLM-driven mood arbiter — Character Engine L3.

Reads the opponent's static persona, their previous mood, and the user's
just-arrived turn, then predicts the next mood. Output is the same 6-dim
`CharacterVector` shape, so the prompt builder and the radar chart consume
it identically — only the source changes from "static catalog value" to
"live mutation".

Design constraints:

* The drift band is **±20 per dim** off the static `character_vector`.
  Without that anchor a frustrated user can ratchet aggression to 100
  over a few turns and the opponent stops feeling like the same person.
* The arbiter call falls back to the previous mood on parse / network
  failure rather than crashing the turn.
* Parser is line-by-line regex (no JSON mode) so the same prompt works
  across DeepSeek / Qwen without per-vendor branching. The strict
  labelled format mirrors `app.services.scenarios.custom._GEN_PROMPT`.

PR-OPT2: in the middle of the arc the arbiter also classifies the
dramatic stage (`next_mood_with_stage`) in the *same* LLM call that
predicts the mood, folding the old separate ArcDirector LLM call into
this one. At the deterministic arc edges (opening / closing) the stage
is already known, so the caller uses the mood-only `next_mood` with the
edge directive injected.

The arbiter does NOT moderate user content — that already ran in
`TurnService.validate_turn_request`. By the time we get here the user
input is safe to feed the LLM.
"""

from __future__ import annotations

import re
from collections.abc import AsyncIterator

import structlog

from app.llm import LLMProvider, Message, TokenUsage
from app.services.scenarios.character_vector import (
    VECTOR_DIMENSIONS,
    CharacterVector,
)
from app.services.sessions.arc_director import (
    STAGE_CLASSIFICATION_GUIDE,
    ArcStage,
    parse_stage,
)

logger = structlog.get_logger(__name__)


# Cap each dim's drift away from the static base. A pure 0/100 swing on
# a single turn would break persona continuity (and the L9 radar would
# jitter). 20 lets a user move the dial but never flip the persona.
MOOD_DRIFT_BAND = 20

# 4s timeout — the arbiter is the FIRST LLM call of the turn so its
# latency directly adds to first-byte. Cheaper to bail to prev_mood
# than to make the user wait 8s for a token.
_ARBITER_TIMEOUT_SEC = 4.0

# The 6-dim definitions + drift rules, shared by both the mood-only and
# the merged stage+mood prompts (PR-OPT2) so there's one source of truth.
_MOOD_DIMS_SPEC = (
    "六个维度，每个 0-100：\n"
    "- aggression（攻击性）：温和→带刺\n"
    "- empathy（共情）：察觉不到用户情绪→精准命中\n"
    "- control（控制欲）：顺着走→强势主导\n"
    "- honesty（诚实）：兜圈子 / 流程话术→直球\n"
    "- stability（稳定）：一推就炸→稳坐钓鱼台\n"
    "- power_gap（权力差）：对等→上位者\n"
    "\n"
    "硬性规则：\n"
    f"1. 每个维度的输出必须落在【基础人格 ±{MOOD_DRIFT_BAND}】之内。"
    "暴躁老板不会因为一句话就变温柔；钝感室友也不会突然共情拉满。\n"
    "2. 用户的话和对手上句没有出现的维度，给出微调 (±3 以内) 即可。\n"
    "3. 用户表现影响方向：用户软（道歉/让步）→ 对手 aggression 降、empathy 升；"
    "用户硬（顶撞/讲理由）→ 对手 stability 降、aggression 微升。"
)

_MOOD_OUTPUT_FORMAT = (
    "严格按以下 6 行格式输出，每行一个键值对，不解释、不要任何额外文字：\n"
    "aggression: <0-100 整数>\n"
    "empathy: <0-100 整数>\n"
    "control: <0-100 整数>\n"
    "honesty: <0-100 整数>\n"
    "stability: <0-100 整数>\n"
    "power_gap: <0-100 整数>"
)

_SYSTEM_PROMPT = (
    "你是对话情绪导演。读完用户刚说的话和对手上句，预测【对手下一句】的情绪向量。"
    "\n\n"
    f"{_MOOD_DIMS_SPEC}"
    "\n\n"
    f"{_MOOD_OUTPUT_FORMAT}"
)

# Merged prompt (PR-OPT2): one call classifies the dramatic stage AND
# predicts the mood. Used only in the arc's middle window — the edges
# already know the stage and use `_SYSTEM_PROMPT`.
_SYSTEM_PROMPT_WITH_STAGE = (
    "你是对话情绪导演。读完用户刚说的话和对手上句，先判断这一轮处在戏剧节奏的哪个阶段，"
    "再预测【对手下一句】的情绪向量，让情绪和阶段一致。"
    "\n\n"
    "阶段三选一：\n"
    f"{STAGE_CLASSIFICATION_GUIDE}"
    "\n\n"
    f"{_MOOD_DIMS_SPEC}"
    "\n\n"
    "严格按以下 7 行格式输出，第一行是阶段，后六行是情绪向量，不解释、不要任何额外文字：\n"
    "stage: <conflict | turning | closing>\n"
    "aggression: <0-100 整数>\n"
    "empathy: <0-100 整数>\n"
    "control: <0-100 整数>\n"
    "honesty: <0-100 整数>\n"
    "stability: <0-100 整数>\n"
    "power_gap: <0-100 整数>"
)

# Safe "keep pressing" default when the merged stage classification is
# unparseable — mirrors the old ArcDirector fallback.
_STAGE_FALLBACK: ArcStage = "conflict"


_DIM_RE = {
    name: re.compile(rf"{name}\s*[:：]\s*(\d+)", re.IGNORECASE) for name in VECTOR_DIMENSIONS
}


class MoodArbiter:
    """Wraps the LLM + parser. Falls back to `prev_mood` on any failure."""

    def __init__(self, llm: LLMProvider) -> None:
        self._llm = llm

    async def next_mood(
        self,
        *,
        character_vector: CharacterVector,
        prev_mood: CharacterVector,
        user_content: str,
        opponent_last_reply: str | None,
        trace_id: str,
        session_id: str,
        arc_directive: str = "",
    ) -> CharacterVector:
        """Compute the opponent's next mood. Returns `prev_mood` unchanged
        on LLM error / parse failure so the turn pipeline never blocks
        on the arbiter.

        `arc_directive` (L2) biases the prediction toward the dramatic
        beat — e.g. a closing directive pulls a hostile opponent toward
        de-escalation. Empty when no arc is wired (pre-L2 paths). Used on
        the deterministic arc edges where the stage is already known; the
        middle window uses `next_mood_with_stage` instead."""
        user_prompt = self._render_user_prompt(
            character_vector=character_vector,
            prev_mood=prev_mood,
            user_content=user_content,
            opponent_last_reply=opponent_last_reply,
            arc_directive=arc_directive,
        )
        messages = [Message.system(_SYSTEM_PROMPT), Message.user(user_prompt)]

        try:
            raw = await _collect(self._llm.stream_chat(messages, timeout=_ARBITER_TIMEOUT_SEC))
        except Exception as exc:  # arbiter never crashes the turn
            logger.warning(
                "mood_arbiter_llm_failed",
                session_id=session_id,
                trace_id=trace_id,
                error=str(exc),
            )
            return prev_mood

        parsed = self._parse(raw)
        if parsed is None:
            logger.warning(
                "mood_arbiter_unparseable",
                session_id=session_id,
                trace_id=trace_id,
                raw=raw[:160],
            )
            return prev_mood

        clamped = _clamp_to_drift_band(parsed, base=character_vector)
        logger.info(
            "mood_arbiter_updated",
            session_id=session_id,
            trace_id=trace_id,
            mood_before=prev_mood.to_dict(),
            mood_after=clamped.to_dict(),
        )
        return clamped

    async def next_mood_with_stage(
        self,
        *,
        character_vector: CharacterVector,
        prev_mood: CharacterVector,
        user_content: str,
        opponent_last_reply: str | None,
        trace_id: str,
        session_id: str,
    ) -> tuple[ArcStage, CharacterVector]:
        """Middle-window path (PR-OPT2): one LLM call classifies the
        dramatic stage AND predicts the next mood, returning
        `(stage, next_mood)`.

        Degrades independently per field so a partial answer is still
        useful: an unparseable stage falls back to `conflict` (keep
        pressing); an unparseable / missing mood block falls back to
        `prev_mood`. Any LLM failure returns `(conflict, prev_mood)` so
        the turn never blocks on the arbiter."""
        user_prompt = self._render_user_prompt(
            character_vector=character_vector,
            prev_mood=prev_mood,
            user_content=user_content,
            opponent_last_reply=opponent_last_reply,
            arc_directive="",
        )
        messages = [Message.system(_SYSTEM_PROMPT_WITH_STAGE), Message.user(user_prompt)]

        try:
            raw = await _collect(self._llm.stream_chat(messages, timeout=_ARBITER_TIMEOUT_SEC))
        except Exception as exc:  # never crashes the turn
            logger.warning(
                "mood_arbiter_stage_llm_failed",
                session_id=session_id,
                trace_id=trace_id,
                error=str(exc),
            )
            return _STAGE_FALLBACK, prev_mood

        stage = parse_stage(raw) or _STAGE_FALLBACK
        parsed = self._parse(raw)
        if parsed is None:
            logger.warning(
                "mood_arbiter_stage_unparseable",
                session_id=session_id,
                trace_id=trace_id,
                stage=stage,
                raw=raw[:160],
            )
            return stage, prev_mood

        clamped = _clamp_to_drift_band(parsed, base=character_vector)
        logger.info(
            "mood_arbiter_stage_updated",
            session_id=session_id,
            trace_id=trace_id,
            stage=stage,
            mood_before=prev_mood.to_dict(),
            mood_after=clamped.to_dict(),
        )
        return stage, clamped

    @staticmethod
    def _render_user_prompt(
        *,
        character_vector: CharacterVector,
        prev_mood: CharacterVector,
        user_content: str,
        opponent_last_reply: str | None,
        arc_directive: str = "",
    ) -> str:
        base = ", ".join(f"{name}={getattr(character_vector, name)}" for name in VECTOR_DIMENSIONS)
        prev = ", ".join(f"{name}={getattr(prev_mood, name)}" for name in VECTOR_DIMENSIONS)
        opponent_line = opponent_last_reply or "（这是用户的第一句，对手只说了开场白）"
        arc_line = f"剧情节奏：{arc_directive}\n" if arc_directive else ""
        return (
            f"基础人格：{base}\n"
            f"上一轮情绪：{prev}\n"
            f"对手上句：{opponent_line}\n"
            f"用户刚说：{user_content}\n"
            f"{arc_line}"
            "预测对手下一句的情绪向量。"
        )

    @staticmethod
    def _parse(raw: str) -> CharacterVector | None:
        """Pull six labelled lines into a CharacterVector. Returns None
        if any dim is missing — the caller falls back to prev_mood."""
        values: dict[str, int] = {}
        for name, pattern in _DIM_RE.items():
            match = pattern.search(raw)
            if match is None:
                return None
            try:
                values[name] = int(match.group(1))
            except (TypeError, ValueError):
                return None
        try:
            return CharacterVector.from_dict(
                {name: max(0, min(100, values[name])) for name in VECTOR_DIMENSIONS}
            )
        except (TypeError, ValueError):
            return None


def _clamp_to_drift_band(predicted: CharacterVector, *, base: CharacterVector) -> CharacterVector:
    """Pull `predicted` back inside `[base - MOOD_DRIFT_BAND, base + MOOD_DRIFT_BAND]`
    per-dim. The LLM is told the rule but doesn't always obey — this
    enforces persona continuity at the data layer so a wild prediction
    can't flip the opponent's personality."""
    clamped: dict[str, int] = {}
    for name in VECTOR_DIMENSIONS:
        anchor = getattr(base, name)
        low = max(0, anchor - MOOD_DRIFT_BAND)
        high = min(100, anchor + MOOD_DRIFT_BAND)
        clamped[name] = max(low, min(high, getattr(predicted, name)))
    return CharacterVector.from_dict(clamped)


async def _collect(stream: AsyncIterator[str]) -> str:
    parts: list[str] = []
    async for chunk in stream:
        parts.append(chunk)
    return "".join(parts)


# `TokenUsage` is part of the public llm package surface — even though
# the arbiter doesn't currently sink usage, re-exporting keeps a single
# import path for future telemetry without breaking dependents.
__all__ = [
    "MOOD_DRIFT_BAND",
    "MoodArbiter",
    "TokenUsage",
]
