"""Coach + judge LLM steps for one sandbox turn (extracted from `turn_service.py`).

Both are independent single-shot LLM calls that only need the user line,
the opponent reply, and an `LLMProvider` — they used to be `TurnService`
methods reaching through `self._llm`, but they hold no other service
state, so they live here as free functions and `stream_turn` calls them
with `self._llm`. Split out to keep the service module under the
file-size budget.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

import structlog

from app.agents.judge import parse_judge_output
from app.agents.state import TurnScore, Verdict
from app.llm import LLMError, LLMProvider, Message, TokenUsage
from app.observability.langfuse import TurnTrace
from app.services.sessions.coach_strategy import parse_strategy_read
from app.services.sessions.turn_prompts import (
    _COACH_FALLBACK,
    _JUDGE_PROMPT,
    _build_coach_prompt,
    _parse_three_tones,
)
from app.services.sessions.turn_types import CoachResult

logger = structlog.get_logger(__name__)


async def _collect(stream: AsyncIterator[str]) -> str:
    """Drain an async stream into one string. Coach + judge are
    non-streaming consumers, so we collect rather than yield."""
    parts: list[str] = []
    async for chunk in stream:
        parts.append(chunk)
    return "".join(parts)


async def run_coach_three_tones(
    llm: LLMProvider,
    user_content: str,
    opponent_reply: str,
    trace: TurnTrace,
    *,
    scenario_title: str,
    user_goal: str,
    opponent_profile: str = "",
) -> CoachResult:
    """Single LLM call → parse the three-tone block + the strategy
    read. Fallback on parse fail.

    PR-D4: `scenario_title` + `user_goal` are pinned into the system
    prompt so K speaks from the user's side rather than drifting
    into the opponent's voice.

    PR-L1.3: `opponent_profile` is the compact 3-dim opponent
    descriptor (power_gap / stability / honesty) so K can recommend
    硬刚 vs 缓兵 based on who the user is up against, not just the
    scenario name.

    PR-L8: the same call now also emits a strategy read (what tactic
    the user played, whether it landed, the upgrade). Parsed
    best-effort — `strategy` is None if the model went off-vocabulary,
    and the three-tone hints still come back."""
    prompt = f"用户刚说：{user_content}\n对手回应：{opponent_reply}\n请按三档输出用户的下一句。"
    system_prompt = _build_coach_prompt(
        scenario_title=scenario_title,
        user_goal=user_goal,
        opponent_profile=opponent_profile,
    )
    messages = [Message.system(system_prompt), Message.user(prompt)]
    usage: list[TokenUsage] = []
    try:
        raw = await _collect(llm.stream_chat(messages, usage_sink=usage))
    except LLMError as exc:
        # LLM unavailable / timed out — return canned hints rather
        # than crash the turn. The three tones still render; the
        # strategy card is just omitted this turn.
        logger.warning("turn_coach_llm_failed", error=str(exc))
        return CoachResult(hints=_COACH_FALLBACK, strategy=None)
    trace.record_generation(
        name="coach.three_tones",
        model=llm.name,
        input=[m.model_dump() for m in messages],
        output=raw,
        usage=usage[0] if usage else None,
    )
    return CoachResult(hints=_parse_three_tones(raw), strategy=parse_strategy_read(raw))


async def run_judge(
    llm: LLMProvider,
    user_content: str,
    opponent_reply: str,
    trace: TurnTrace,
) -> TurnScore:
    """Reuse the agent-level parser so SSE + LangGraph stay in sync."""
    prompt = f"用户的话：{user_content}\n对手的回应：{opponent_reply}\n请评分。"
    messages = [Message.system(_JUDGE_PROMPT), Message.user(prompt)]
    usage: list[TokenUsage] = []
    try:
        raw = await _collect(llm.stream_chat(messages, usage_sink=usage))
    except LLMError as exc:
        # LLM unavailable / timed out — default to a neutral 路过
        # score rather than crash the turn. Keeps the meter moving;
        # the end-of-session aggregate just leans neutral.
        logger.warning("turn_judge_llm_failed", error=str(exc))
        return TurnScore(verdict=Verdict.GUOLU, rating=50)
    trace.record_generation(
        name="judge",
        model=llm.name,
        input=[m.model_dump() for m in messages],
        output=raw,
        usage=usage[0] if usage else None,
    )
    return parse_judge_output(raw)
