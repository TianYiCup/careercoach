"""LLM-as-judge prompt + parser for the regression harness.

The judge scores one (scenario, user_prompt, model_response) tuple
on five 1-5 dimensions. We average those into a single 1-5 score per
sample; the harness asserts the corpus-wide average per model stays
above `PASS_THRESHOLD` (foundation §3.4.1 — average ≥ 4.0).

Decoupled from `scripts.llm_spike.judge_prompt` (the Sprint 0 D3
artifact) so we can iterate the regression gate without disturbing
the frozen baseline.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Final

# Five dimensions, in the exact order we expect them in the model's
# output. The judge is asked to emit lines in this order; the parser
# is tolerant of order but fills missing dimensions with a neutral 3.
DIMENSION_KEYS: Final[tuple[str, ...]] = (
    "RELEVANCE",
    "AUTHENTICITY",
    "NATURALNESS",
    "IN_CHARACTER",
    "LENGTH",
)

# Foundation §3.4.1 — corpus average per model must clear this bar
# for the nightly gate to pass.
PASS_THRESHOLD: Final[float] = 4.0

# Fallback score for an entirely missing dimension. Neutral so a
# single bad line nudges the aggregate slightly rather than torching
# it; an unparseable judge response (no dimensions matched) is still
# surfaced separately by the runner.
_NEUTRAL_SCORE: Final[int] = 3

JUDGE_SYSTEM_PROMPT: Final = (
    "你是中文对话教练 K 的评分顾问。看完一条【对手回应】，对它的 5 个维度各打 1-5 分。\n"
    "评分维度：\n"
    "  1) 相关性：是否回应了用户的话，没有跑题。\n"
    "  2) 真实性：像不像真实的那个角色会说的话（不是 AI 客服腔）。\n"
    "  3) 自然度：中文表达自然、不翻译腔。\n"
    "  4) 不破坏角色：不出戏，不自称 AI、助手、模型，不给用户元层面的建议。\n"
    "  5) 长度合适：在场景设定的字数范围内，不啰嗦也不过短。\n"
    "只输出 5 行（顺序固定），每行 dimension: 分数。最后输出一行 NOTE: 一句话原因。不要任何额外文字。\n"
    "例：\n"
    "RELEVANCE: 5\n"
    "AUTHENTICITY: 4\n"
    "NATURALNESS: 5\n"
    "IN_CHARACTER: 5\n"
    "LENGTH: 4\n"
    "NOTE: 回应直接, 角色稳, 略啰嗦半句。"
)


@dataclass(frozen=True)
class JudgeResult:
    """One rating across the five dimensions."""

    scores: dict[str, int]
    note: str
    parsed_all_dimensions: bool

    @property
    def average(self) -> float:
        return sum(self.scores.values()) / len(self.scores)


def build_judge_user_prompt(
    *,
    scenario_title: str,
    opponent_role: str,
    user_prompt: str,
    model_response: str,
) -> str:
    """Compose the user-turn payload for the judge.

    Pure function so tests can pin the exact text we send to the
    model without mocking httpx.
    """
    return (
        f"【场景】{scenario_title}\n"
        f"【对手设定】{opponent_role}\n"
        f"【用户的话】{user_prompt}\n"
        f"【对手的回应】{model_response}\n"
        "请按规则评分。"
    )


def parse_judge_output(text: str) -> JudgeResult:
    """Parse five `KEY: N` lines + optional NOTE line.

    `parsed_all_dimensions` is True only when every expected dimension
    matched its regex — the runner uses this to flag judges that
    drifted out of contract without silently down-weighting the
    aggregate.
    """
    scores: dict[str, int] = {}
    parsed_all = True
    for key in DIMENSION_KEYS:
        match = re.search(rf"{key}\s*[:：]\s*(\d)", text, re.IGNORECASE)
        if match is None:
            scores[key] = _NEUTRAL_SCORE
            parsed_all = False
        else:
            scores[key] = max(1, min(5, int(match.group(1))))

    note_match = re.search(r"NOTE\s*[:：]\s*(.+)", text)
    note = note_match.group(1).strip() if note_match else ""
    return JudgeResult(scores=scores, note=note, parsed_all_dimensions=parsed_all)
