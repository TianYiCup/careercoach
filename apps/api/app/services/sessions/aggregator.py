"""Build the per-session 5-dim `Score` from accumulated turn history.

This is the seam that PR 4a marked with `_stub_score()`. Inputs:

* `turns: list[TurnRecord]` — every user/opponent exchange the session
  saw, with each turn's `TurnScore` already attached.
* `llm: LLMProvider` — used once at the end to ask for a holistic
  summary across the whole conversation.

Output: a fully-populated `app.schemas.sessions.Score` ready to ship
on `/end` and persist into `SessionScoreRepository` for sharecards.

Three branches, in priority order:

1. **No turns** — user ended without speaking. Skip the LLM entirely
   and return a neutral guolu/zeroes summary so the response stays
   well-formed; the canary log notes the empty session.

2. **LLM summary succeeds** — preferred path. One extra LLM call
   summarising the full transcript yields the 5 dims + highlights +
   failures. Cheap because /end isn't streamed (latency matters less
   than /turns).

3. **LLM fails or parse fails** — fall back to a mechanical aggregate
   from the per-turn ratings. Every dim collapses to `round(avg/10)`
   so the response is well-typed and the user still sees a result.
   Highlights/failures pull from a verdict-mode template. Logged as
   `session_summary_fallback` so we can monitor how often this fires.

`result` is *never* asked from the LLM — it's computed from the
five-dim average right here. Boundary stays in code, prompt stays
short, and the dim→verdict mapping is testable in isolation.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import AsyncIterator, Iterable

import structlog

from app.agents.state import Verdict
from app.agents.summarizer import (
    SUMMARIZER_SYSTEM_PROMPT,
    SessionSummary,
    parse_summary_output,
)
from app.llm import LLMError, LLMProvider, Message
from app.schemas.sessions import Score, ScoreResult
from app.services.sessions.turn_repository import TurnRecord

logger = structlog.get_logger(__name__)


_EMPTY_SESSION_SUMMARY = SessionSummary(
    aura=0,
    logic=0,
    emotion=0,
    professionalism=0,
    goal_achieve=0,
    highlights="未开始练习",
    failures="本次未发言",
)

# Highlight/failure copy for each verdict mode in the mechanical
# fallback. Short enough to stay under the 30-character soft cap the
# UI's ShareCard renderer assumes.
_VERDICT_TEMPLATES: dict[Verdict, tuple[str, str]] = {
    Verdict.SHENFENG: (
        "整体表达稳，把控住了局面",
        "个别回合还能更直接",
    ),
    Verdict.GUOLU: (
        "态度稳得住，没翻车",
        "缺一两句决定性回应",
    ),
    Verdict.FANCHE: (
        "敢开口已经赢了一半",
        "让步过早，没守住底线",
    ),
}

_RESULT_SHENFENG_FLOOR = 8.0
_RESULT_FANCHE_CEILING = 5.0


async def aggregate_session_score(
    *,
    turns: list[TurnRecord],
    llm: LLMProvider,
    user_goal: str,
    scenario_title: str,
) -> Score:
    """Single public entry. See module docstring for branch behaviour."""
    if not turns:
        logger.info("session_summary_empty", reason="no_turns_recorded")
        return _summary_to_score(_EMPTY_SESSION_SUMMARY)

    summary = await _try_llm_summary(
        turns=turns,
        llm=llm,
        user_goal=user_goal,
        scenario_title=scenario_title,
    )
    if summary is None:
        summary = _mechanical_summary(turns)

    return _summary_to_score(summary)


async def _try_llm_summary(
    *,
    turns: list[TurnRecord],
    llm: LLMProvider,
    user_goal: str,
    scenario_title: str,
) -> SessionSummary | None:
    """Run the LLM summarizer; return None on any failure so the
    caller can fall back without juggling exceptions."""
    transcript = _format_transcript(turns)
    user_prompt = (
        f"场景：{scenario_title}\n"
        f"用户目标：{user_goal}\n"
        f"---\n对话记录：\n{transcript}\n---\n请按 7 行格式输出评分。"
    )
    messages = [
        Message.system(SUMMARIZER_SYSTEM_PROMPT),
        Message.user(user_prompt),
    ]
    try:
        raw = await _collect(llm.stream_chat(messages))
    except LLMError as exc:
        logger.warning(
            "session_summary_fallback",
            reason="llm_error",
            provider_error=type(exc).__name__,
            message=str(exc)[:200],
        )
        return None

    summary = parse_summary_output(raw)
    if summary is None:
        # parse_summary_output already logged the specific reason.
        logger.warning("session_summary_fallback", reason="unparseable")
    return summary


def _mechanical_summary(turns: list[TurnRecord]) -> SessionSummary:
    """Derive dims + narrative from `TurnScore` ratings alone.

    Every dim gets the same number — there's no per-dim signal at this
    layer, but the response stays well-typed and the verdict mode still
    picks the right narrative. The user sees a plausible result instead
    of a 500.
    """
    avg_rating = sum(t.turn_score.rating for t in turns) / len(turns)
    dim_value = max(0, min(10, round(avg_rating / 10)))

    verdict_mode = _modal_verdict(t.turn_score.verdict for t in turns)
    highlights, failures = _VERDICT_TEMPLATES[verdict_mode]

    return SessionSummary(
        aura=dim_value,
        logic=dim_value,
        emotion=dim_value,
        professionalism=dim_value,
        goal_achieve=dim_value,
        highlights=highlights,
        failures=failures,
    )


def _summary_to_score(summary: SessionSummary) -> Score:
    """Adapter from internal dataclass to the HTTP envelope."""
    return Score(
        aura=summary.aura,
        logic=summary.logic,
        emotion=summary.emotion,
        professionalism=summary.professionalism,
        goal_achieve=summary.goal_achieve,
        highlights=summary.highlights,
        failures=summary.failures,
        result=_derive_result(summary),
    )


def _derive_result(summary: SessionSummary) -> ScoreResult:
    """Map five-dim average to the verdict literal.

    Bands match `schemas/sessions.py::Score.result` docstring:
    封神 ≥ 8 / 路过 5–7 / 翻车 < 5.
    """
    avg = (
        summary.aura
        + summary.logic
        + summary.emotion
        + summary.professionalism
        + summary.goal_achieve
    ) / 5
    if avg >= _RESULT_SHENFENG_FLOOR:
        return "shenfeng"
    if avg < _RESULT_FANCHE_CEILING:
        return "fanche"
    return "guolu"


def _modal_verdict(verdicts: Iterable[Verdict]) -> Verdict:
    """Pick the most-common verdict, breaking ties by leaning negative.

    Tie-break direction matters: if half the turns are fanche, we want
    the narrative to reflect the slip-ups, not the upside.
    """
    counts = Counter(verdicts)
    if not counts:
        return Verdict.GUOLU
    max_count = max(counts.values())
    for verdict in (Verdict.FANCHE, Verdict.GUOLU, Verdict.SHENFENG):
        if counts.get(verdict, 0) == max_count:
            return verdict
    return Verdict.GUOLU  # pragma: no cover — loop above is exhaustive


def _format_transcript(turns: list[TurnRecord]) -> str:
    """Render the turn history into a compact, LLM-friendly transcript.

    One line per speaker so the prompt stays short on token count for
    long sessions; turn numbering helps the model anchor highlights to
    specific exchanges.
    """
    lines: list[str] = []
    for index, turn in enumerate(turns, start=1):
        lines.append(f"[轮 {index}] 用户：{turn.user_content}")
        lines.append(f"        对手：{turn.opponent_reply}")
    return "\n".join(lines)


async def _collect(stream: AsyncIterator[str]) -> str:
    """Drain an async chunk stream into a single string."""
    parts: list[str] = []
    async for chunk in stream:
        parts.append(chunk)
    return "".join(parts)


__all__ = ["aggregate_session_score"]
