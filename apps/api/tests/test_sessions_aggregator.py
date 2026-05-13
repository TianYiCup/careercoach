"""Tests for `app.services.sessions.aggregator`.

Three branches under test (mirroring the module docstring):
  1. empty session → static guolu-band Score
  2. LLM happy path → parsed five dims + derived result
  3. LLM fails (or emits garbage) → mechanical fallback

Plus the `_derive_result` math, since it's the only place the
shenfeng / guolu / fanche boundary lives.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, datetime

import pytest
from app.agents.state import TurnScore, Verdict
from app.llm import LLMAuthError, Message
from app.services.sessions.aggregator import aggregate_session_score
from app.services.sessions.turn_repository import CoachHintTrio, TurnRecord


class _CannedLLM:
    name = "canned"

    def __init__(self, output: str) -> None:
        self.output = output
        self.calls = 0

    async def stream_chat(
        self,
        messages: list[Message],
        *,
        temperature: float = 0.7,
        timeout: float = 30.0,
    ) -> AsyncIterator[str]:
        del messages, temperature, timeout
        self.calls += 1
        yield self.output


class _BrokenLLM:
    name = "broken"

    def __init__(self) -> None:
        self.calls = 0

    async def stream_chat(
        self,
        messages: list[Message],
        *,
        temperature: float = 0.7,
        timeout: float = 30.0,
    ) -> AsyncIterator[str]:
        del messages, temperature, timeout
        self.calls += 1
        raise LLMAuthError("no creds", provider=self.name)
        yield ""  # pragma: no cover


def _turn(
    *,
    rating: int,
    verdict: Verdict = Verdict.GUOLU,
    user_content: str = "周末有事",
    opponent_reply: str = "项目谁顶？",
) -> TurnRecord:
    return TurnRecord(
        turn_id="t_x",
        session_id="ses_x",
        user_content=user_content,
        opponent_reply=opponent_reply,
        coach_hint=CoachHintTrio(safe="s", aggressive="a", humor="h"),
        turn_score=TurnScore(verdict=verdict, rating=rating),
        created_at=datetime.now(UTC),
    )


async def test_empty_session_returns_zero_score_without_calling_llm() -> None:
    """When the user `/end`s with no turns recorded, skip the LLM
    entirely — there's nothing to summarise."""
    llm = _CannedLLM(
        "AURA: 9\nLOGIC: 9\nEMOTION: 9\nPROFESSIONALISM: 9\nGOAL_ACHIEVE: 9\nHIGHLIGHTS: x\nFAILURES: y"
    )

    score = await aggregate_session_score(
        turns=[],
        llm=llm,
        user_goal="保住周末",
        scenario_title="周末加班谈判",
    )

    assert llm.calls == 0
    assert score.aura == 0
    assert score.logic == 0
    assert score.goal_achieve == 0
    assert score.result == "fanche"  # average 0 < 5
    assert score.highlights == "未开始练习"
    assert score.failures == "本次未发言"


async def test_llm_summary_drives_score_when_output_parses() -> None:
    """Happy path — LLM returns a clean 7-line summary, aggregator
    parses it, dims map straight through, result derives from avg."""
    llm = _CannedLLM(
        "AURA: 9\n"
        "LOGIC: 8\n"
        "EMOTION: 9\n"
        "PROFESSIONALISM: 9\n"
        "GOAL_ACHIEVE: 8\n"
        "HIGHLIGHTS: 顶住压力还反将了一军\n"
        "FAILURES: 末段语速过快"
    )

    score = await aggregate_session_score(
        turns=[_turn(rating=80, verdict=Verdict.SHENFENG)],
        llm=llm,
        user_goal="保住周末",
        scenario_title="周末加班谈判",
    )

    assert llm.calls == 1
    assert score.aura == 9
    assert score.logic == 8
    assert score.goal_achieve == 8
    # Average = (9+8+9+9+8)/5 = 8.6 → shenfeng band.
    assert score.result == "shenfeng"
    assert score.highlights == "顶住压力还反将了一军"
    assert score.failures == "末段语速过快"


async def test_llm_failure_falls_back_to_mechanical_aggregation() -> None:
    """LLMAuthError mid-call must trigger the mechanical path. Every
    dim collapses to round(avg_rating/10); narrative comes from the
    modal verdict's template copy."""
    llm = _BrokenLLM()
    turns = [
        _turn(rating=50, verdict=Verdict.GUOLU),
        _turn(rating=70, verdict=Verdict.GUOLU),
    ]

    score = await aggregate_session_score(
        turns=turns,
        llm=llm,
        user_goal="保住周末",
        scenario_title="周末加班谈判",
    )

    # avg_rating = 60 → dim_value = round(60/10) = 6
    assert score.aura == 6
    assert score.logic == 6
    assert score.goal_achieve == 6
    # GUOLU is the modal verdict; check the template copy made it through.
    assert score.highlights == "态度稳得住，没翻车"
    assert score.failures == "缺一两句决定性回应"
    assert score.result == "guolu"


async def test_unparseable_llm_output_falls_back_to_mechanical() -> None:
    """Garbage output is treated the same as LLMError — the user still
    gets a valid Score, not a parse exception."""
    llm = _CannedLLM("the model wrote a haiku here")

    score = await aggregate_session_score(
        turns=[_turn(rating=30, verdict=Verdict.FANCHE)],
        llm=llm,
        user_goal="保住周末",
        scenario_title="周末加班谈判",
    )

    assert llm.calls == 1  # parser tried before falling back
    # avg_rating 30 → dim 3, FANCHE template:
    assert score.aura == 3
    assert score.result == "fanche"
    assert score.highlights == "敢开口已经赢了一半"
    assert score.failures == "让步过早，没守住底线"


async def test_modal_verdict_tie_break_prefers_fanche() -> None:
    """Tied verdict counts must lean toward the negative narrative —
    sharecard copy should reflect risk, not best-case spin."""
    llm = _BrokenLLM()
    turns = [
        _turn(rating=80, verdict=Verdict.SHENFENG),
        _turn(rating=20, verdict=Verdict.FANCHE),
    ]

    score = await aggregate_session_score(
        turns=turns,
        llm=llm,
        user_goal="保住周末",
        scenario_title="周末加班谈判",
    )

    # avg_rating = 50 → dim 5
    assert score.aura == 5
    # FANCHE wins the tie, so its template appears even though avg
    # rating sits in the guolu band.
    assert score.highlights == "敢开口已经赢了一半"
    # Result is derived from dim average, not the modal verdict:
    # avg dim = 5 → guolu band (≥ 5, < 8).
    assert score.result == "guolu"


@pytest.mark.parametrize(
    ("dim", "expected"),
    [
        (0, "fanche"),
        (4, "fanche"),
        (5, "guolu"),
        (7, "guolu"),
        (8, "shenfeng"),
        (10, "shenfeng"),
    ],
)
async def test_result_band_boundaries(dim: int, expected: str) -> None:
    """Confirm the design-spec bands: 封神 ≥ 8 / 路过 5–7 / 翻车 < 5."""
    llm = _BrokenLLM()
    # Make rating produce `dim` after the /10 round in the mechanical path.
    # round(rating/10) = dim → rating = dim*10.
    score = await aggregate_session_score(
        turns=[_turn(rating=dim * 10, verdict=Verdict.GUOLU)],
        llm=llm,
        user_goal="x",
        scenario_title="x",
    )
    assert score.result == expected
