"""Parser tests for `app.agents.summarizer`.

Drives `parse_summary_output` against happy / malformed / out-of-range
inputs so the aggregator can rely on `None` meaning "fall back" and a
returned dataclass meaning "every field is valid".
"""

from __future__ import annotations

from app.agents.summarizer import SessionSummary, parse_summary_output


def test_parse_summary_output_happy_path() -> None:
    raw = (
        "AURA: 8\n"
        "LOGIC: 7\n"
        "EMOTION: 6\n"
        "PROFESSIONALISM: 9\n"
        "GOAL_ACHIEVE: 7\n"
        "HIGHLIGHTS: 节奏掌控不错\n"
        "FAILURES: 末段语气稍硬"
    )

    summary = parse_summary_output(raw)

    assert summary == SessionSummary(
        aura=8,
        logic=7,
        emotion=6,
        professionalism=9,
        goal_achieve=7,
        highlights="节奏掌控不错",
        failures="末段语气稍硬",
    )


def test_parse_summary_output_tolerates_whitespace_and_case() -> None:
    raw = (
        "aura  :   5\n"
        "Logic:6\n"
        "EMOTION: 5\n"
        "professionalism :   7\n"
        "GOAL_ACHIEVE:8\n"
        "highlights:  顶住了压力 \n"
        "Failures: 反问可以更尖"
    )

    summary = parse_summary_output(raw)

    assert summary is not None
    assert summary.aura == 5
    assert summary.logic == 6
    assert summary.highlights == "顶住了压力"
    assert summary.failures == "反问可以更尖"


def test_parse_summary_output_returns_none_when_dim_missing() -> None:
    raw = (
        "AURA: 7\n"
        "LOGIC: 6\n"
        # EMOTION missing
        "PROFESSIONALISM: 8\n"
        "GOAL_ACHIEVE: 5\n"
        "HIGHLIGHTS: 还行\n"
        "FAILURES: 一般"
    )

    assert parse_summary_output(raw) is None


def test_parse_summary_output_returns_none_when_dim_out_of_range() -> None:
    raw = (
        "AURA: 99\n"  # > 10
        "LOGIC: 6\n"
        "EMOTION: 5\n"
        "PROFESSIONALISM: 8\n"
        "GOAL_ACHIEVE: 5\n"
        "HIGHLIGHTS: 一般\n"
        "FAILURES: 一般"
    )

    assert parse_summary_output(raw) is None


def test_parse_summary_output_returns_none_when_narrative_missing() -> None:
    raw = (
        "AURA: 7\nLOGIC: 7\nEMOTION: 7\nPROFESSIONALISM: 7\nGOAL_ACHIEVE: 7\n"
        # no HIGHLIGHTS or FAILURES
    )

    assert parse_summary_output(raw) is None


def test_parse_summary_output_returns_none_on_garbage_input() -> None:
    assert parse_summary_output("the model decided to write a poem instead") is None
    assert parse_summary_output("") is None
