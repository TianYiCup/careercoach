"""End-to-end tests for the LangGraph 3-node orchestration.

We don't hit the network — a `_ScriptedProvider` returns pre-canned
responses based on which system prompt it sees, so we get a real
exercise of the StateGraph wiring without external dependencies.
"""

from collections.abc import AsyncIterator

import pytest
from app.agents import SessionState, build_graph, make_initial_state
from app.agents.judge import (
    JUDGE_SYSTEM_PROMPT,
    parse_judge_output,
)
from app.agents.state import Score, Verdict
from app.llm import LLMProvider, Message


class _ScriptedProvider:
    """In-memory provider that picks a response by system prompt prefix.

    Each test installs a tiny script: `{prompt_keyword: response}`,
    and the provider matches the active call's system prompt against
    those keywords. Any unmatched call raises so we notice if a node
    is talking to the wrong slot in the graph.
    """

    name = "scripted"

    def __init__(self, script: dict[str, str]) -> None:
        self._script = script
        self.calls: list[Message] = []

    async def stream_chat(
        self,
        messages: list[Message],
        *,
        temperature: float = 0.7,
        timeout: float = 8.0,
    ) -> AsyncIterator[str]:
        system = messages[0].content if messages else ""
        self.calls.append(messages[0])
        for keyword, response in self._script.items():
            if keyword in system:
                # Yield in two halves so we also exercise the
                # accumulator's multi-chunk path.
                yield response[: len(response) // 2]
                yield response[len(response) // 2 :]
                return
        raise AssertionError(f"unscripted system prompt: {system[:60]!r}")


def _scripted_provider(
    *,
    opponent: str = "对手的回应",
    hint: str = "K 的提示",
    judge: str = "VERDICT: pass\nRATING: 70",
) -> LLMProvider:
    return _ScriptedProvider(
        {
            "你扮演用户练习对话中的对手": opponent,
            "你是教练 K": hint,
            "你是评委": judge,
        }
    )


@pytest.fixture
def initial_state() -> SessionState:
    return make_initial_state(
        scenario_id="campus.tutor.scolding",
        history=[Message.assistant("(对手开场白)")],
        user_turn="老师我已经很努力了",
    )


async def test_graph_runs_end_to_end_and_populates_every_field(
    initial_state: SessionState,
) -> None:
    provider = _scripted_provider()
    graph = build_graph(provider)

    final: SessionState = await graph.ainvoke(initial_state)

    assert final["opponent_reply"] == "对手的回应"
    assert final["coach_hint"] == "K 的提示"
    assert final["score"] == Score(verdict=Verdict.PASS, rating=70)


async def test_input_state_is_preserved_through_graph(
    initial_state: SessionState,
) -> None:
    graph = build_graph(_scripted_provider())

    final: SessionState = await graph.ainvoke(initial_state)

    assert final["scenario_id"] == initial_state["scenario_id"]
    assert final["user_turn"] == initial_state["user_turn"]
    assert final["history"] == initial_state["history"]


async def test_nodes_fire_in_order(initial_state: SessionState) -> None:
    provider = _ScriptedProvider(
        {
            "你扮演用户练习对话中的对手": "x",
            "你是教练 K": "y",
            "你是评委": "VERDICT: godlike\nRATING: 95",
        }
    )
    graph = build_graph(provider)

    await graph.ainvoke(initial_state)

    keywords = [call.content for call in provider.calls]
    # Roleplay first, coach second, judge last — established by edges.
    assert "对手" in keywords[0]
    assert "教练 K" in keywords[1]
    assert keywords[2] == JUDGE_SYSTEM_PROMPT


async def test_judge_failure_to_parse_degrades_to_neutral(
    initial_state: SessionState,
) -> None:
    provider = _scripted_provider(judge="??? this is not the contract ???")
    graph = build_graph(provider)

    final: SessionState = await graph.ainvoke(initial_state)

    # Fallback per judge.py: pass / 50, so the graph still completes.
    assert final["score"] == Score(verdict=Verdict.PASS, rating=50)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (
            "VERDICT: godlike\nRATING: 98",
            Score(verdict=Verdict.GODLIKE, rating=98),
        ),
        (
            "VERDICT: pass\nRATING: 50",
            Score(verdict=Verdict.PASS, rating=50),
        ),
        (
            "VERDICT: fail\nRATING: 12",
            Score(verdict=Verdict.FAIL, rating=12),
        ),
        # Tolerant to whitespace, case, and surrounding noise.
        (
            "  verdict:  PASS  \n  rating:    77  \n",
            Score(verdict=Verdict.PASS, rating=77),
        ),
        (
            "preamble\nVERDICT: fail\nrating: 1\ntrailer",
            Score(verdict=Verdict.FAIL, rating=1),
        ),
    ],
)
def test_parse_judge_output_happy_paths(raw: str, expected: Score) -> None:
    assert parse_judge_output(raw) == expected


def test_parse_judge_output_clamps_rating_in_range() -> None:
    assert parse_judge_output("VERDICT: pass\nRATING: 999").rating == 100
    # Negative scores can't reach the regex (\d only), so this asserts
    # the natural max clamp; lower-bound clamping is exercised by the
    # ge=0 validation built into the Score model.


def test_parse_judge_output_falls_back_on_unknown_verdict() -> None:
    out = parse_judge_output("VERDICT: 飘逸\nRATING: 80")
    assert out == Score(verdict=Verdict.PASS, rating=50)


def test_parse_judge_output_falls_back_when_fields_missing() -> None:
    out = parse_judge_output("RATING: 80")  # no verdict line
    assert out == Score(verdict=Verdict.PASS, rating=50)
