"""Session-end summarizer — produces a 5-dim Score from the full turn history.

PR 4b's per-turn judge gives `(verdict, rating)` only. The HTTP
`Score` envelope demands five dimensions (aura/logic/emotion/
professionalism/goal_achieve) plus narrative highlights + failures.
Rather than expand `TurnScore` to five dims (which would balloon
the judge prompt and rewrite every per-turn assertion), this module
runs one extra LLM call at `/end` over the entire conversation and
parses the result.

Output contract (LLM is asked to emit exactly these 7 lines):

    AURA: 0-10
    LOGIC: 0-10
    EMOTION: 0-10
    PROFESSIONALISM: 0-10
    GOAL_ACHIEVE: 0-10
    HIGHLIGHTS: <≤30字>
    FAILURES: <≤30字>

The `result` enum (shenfeng/guolu/fanche) is *not* asked from the
LLM — it's derived deterministically from the five-dim average so
the boundary stays in our code, not the prompt.

Parse failures fall through to a neutral summary so `/end` never
500s on a flaky model. The aggregator owns the LLM-vs-mechanical
choice; this module only handles prompt + parse.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

import structlog

logger = structlog.get_logger(__name__)


SUMMARIZER_SYSTEM_PROMPT = (
    "你是评委。看完用户和对手的整段对话，对【用户】整体表现做一份 5 维评分 + 总结。\n"
    "严格按以下 7 行格式输出，不要解释、不要任何额外文字：\n"
    "AURA: 0-10 的整数（气场 / presence）\n"
    "LOGIC: 0-10 的整数\n"
    "EMOTION: 0-10 的整数（情绪管理）\n"
    "PROFESSIONALISM: 0-10 的整数\n"
    "GOAL_ACHIEVE: 0-10 的整数（与用户目标的接近度）\n"
    "HIGHLIGHTS: 不超过 30 字，用户表现的亮点\n"
    "FAILURES: 不超过 30 字，用户的失分点"
)


@dataclass(frozen=True)
class SessionSummary:
    """LLM summarizer output — the 5 dims + narrative pair.

    The HTTP `Score.result` literal is derived from these five values
    in `app.services.sessions.aggregator`, not parsed from the LLM.
    """

    aura: int
    logic: int
    emotion: int
    professionalism: int
    goal_achieve: int
    highlights: str
    failures: str


_DIM_PATTERNS: dict[str, re.Pattern[str]] = {
    "aura": re.compile(r"AURA\s*:\s*(\d{1,2})", re.IGNORECASE),
    "logic": re.compile(r"LOGIC\s*:\s*(\d{1,2})", re.IGNORECASE),
    "emotion": re.compile(r"EMOTION\s*:\s*(\d{1,2})", re.IGNORECASE),
    "professionalism": re.compile(r"PROFESSIONALISM\s*:\s*(\d{1,2})", re.IGNORECASE),
    "goal_achieve": re.compile(r"GOAL_ACHIEVE\s*:\s*(\d{1,2})", re.IGNORECASE),
}
_HIGHLIGHTS_RE = re.compile(r"HIGHLIGHTS\s*:\s*(.+)", re.IGNORECASE)
_FAILURES_RE = re.compile(r"FAILURES\s*:\s*(.+)", re.IGNORECASE)


def parse_summary_output(text: str) -> SessionSummary | None:
    """Best-effort parse of the 7-line summarizer contract.

    Returns `None` if any field is missing or out of range — partial
    parses would mix LLM signal with stub values, which is worse than
    a clean fallback. The aggregator decides what to do on `None`.

    Public so unit tests can drive the parser without an LLM.
    """
    dims: dict[str, int] = {}
    for name, pattern in _DIM_PATTERNS.items():
        match = pattern.search(text)
        if match is None:
            logger.warning("summarizer_missing_dim", dim=name, raw=text[:200])
            return None
        value = int(match.group(1))
        if not 0 <= value <= 10:
            logger.warning("summarizer_dim_out_of_range", dim=name, value=value)
            return None
        dims[name] = value

    highlights_match = _HIGHLIGHTS_RE.search(text)
    failures_match = _FAILURES_RE.search(text)
    if highlights_match is None or failures_match is None:
        logger.warning("summarizer_missing_narrative", raw=text[:200])
        return None

    return SessionSummary(
        **dims,
        highlights=highlights_match.group(1).strip(),
        failures=failures_match.group(1).strip(),
    )


__all__ = [
    "SUMMARIZER_SYSTEM_PROMPT",
    "SessionSummary",
    "parse_summary_output",
]
