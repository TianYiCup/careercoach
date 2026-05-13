"""Judge node — scores the user's most recent turn.

The judge LLM is asked to emit a small structured payload:

    VERDICT: <shenfeng|guolu|fanche>
    RATING: <0-100>

JSON would be more robust, but parsing two lines keeps the prompt
short for Sprint 0 and the parser is exhaustively unit-tested.
Anything we can't parse degrades to a neutral `guolu` / 50 so the
graph still completes (callers can detect the degradation via
`TurnScore.rating == 50` + the `judge_unparseable` log line they'll
see).

Verdict literals align with `app.schemas.sessions.ScoreResult` —
design-spec §3.0 评分语义: 封神 / 路过 / 翻车.
"""

from __future__ import annotations

import re
from collections.abc import Awaitable, Callable

import structlog

from app.agents._stream import stream_to_text
from app.agents.state import SessionState, TurnScore, Verdict
from app.llm import LLMProvider, Message

logger = structlog.get_logger(__name__)

JUDGE_SYSTEM_PROMPT = (
    "你是评委。看完用户与对手的对话，对【用户的话】给一个评分。\n"
    "只输出两行：\n"
    "VERDICT: shenfeng | guolu | fanche\n"
    "RATING: 0-100 的整数\n"
    "不要解释，不要任何额外文字。"
)

_VERDICT_RE = re.compile(r"VERDICT\s*:\s*(\w+)", re.IGNORECASE)
_RATING_RE = re.compile(r"RATING\s*:\s*(\d{1,3})", re.IGNORECASE)

# Fallback when the model misbehaves — neutral verdict so the user
# still sees a result, but caller can tell something was off.
_FALLBACK = TurnScore(verdict=Verdict.GUOLU, rating=50)


def parse_judge_output(text: str) -> TurnScore:
    """Best-effort parse of the two-line judge contract.

    Public so tests can drive parsing directly without spinning up
    the whole graph.
    """
    verdict_match = _VERDICT_RE.search(text)
    rating_match = _RATING_RE.search(text)
    if verdict_match is None or rating_match is None:
        logger.warning("judge_unparseable", raw=text[:200])
        return _FALLBACK

    try:
        verdict = Verdict(verdict_match.group(1).lower())
    except ValueError:
        logger.warning("judge_unknown_verdict", raw=text[:200])
        return _FALLBACK

    rating = max(0, min(100, int(rating_match.group(1))))
    return TurnScore(verdict=verdict, rating=rating)


def make_judge_node(
    provider: LLMProvider,
) -> Callable[[SessionState], Awaitable[SessionState]]:
    async def node(state: SessionState) -> SessionState:
        opponent_reply = state.get("opponent_reply", "")
        prompt = f"用户的话：{state['user_turn']}\n对手的回应：{opponent_reply}\n请评分。"
        messages: list[Message] = [
            Message.system(JUDGE_SYSTEM_PROMPT),
            Message.user(prompt),
        ]
        raw = await stream_to_text(provider.stream_chat(messages))
        return SessionState(turn_score=parse_judge_output(raw))

    return node
