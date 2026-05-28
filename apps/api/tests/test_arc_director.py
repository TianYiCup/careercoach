"""Unit tests for the ArcDirector (Character Engine L2).

Two layers:
  * deterministic edge guards — opening on the first turns, closing in
    the tail before the cap, both without touching the LLM;
  * the middle-window LLM calibration — parses conflict/turning/closing
    and falls back to conflict on garbage / failure.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

from app.llm import Message
from app.services.sessions.arc_director import ArcDirector


class _ScriptedLLM:
    """Yields a fixed response and records whether it was called — lets
    the edge-guard tests assert the LLM was NOT consulted."""

    name = "scripted"

    def __init__(self, response: str) -> None:
        self._response = response
        self.calls = 0

    async def stream_chat(
        self,
        messages: list[Message],
        *,
        temperature: float = 0.7,
        timeout: float = 8.0,
        usage_sink: list | None = None,
    ) -> AsyncIterator[str]:
        _ = (messages, temperature, timeout, usage_sink)
        self.calls += 1
        yield self._response


class _RaisingLLM:
    name = "raising"

    def __init__(self) -> None:
        self.calls = 0

    async def stream_chat(
        self,
        messages: list[Message],
        *,
        temperature: float = 0.7,
        timeout: float = 8.0,
        usage_sink: list | None = None,
    ) -> AsyncIterator[str]:
        _ = (messages, temperature, timeout, usage_sink)
        self.calls += 1
        raise TimeoutError("simulated arc timeout")
        yield ""  # pragma: no cover — makes this an async generator


async def _resolve(llm: object, *, turn_index: int, turns_left: int):
    director = ArcDirector(llm)  # type: ignore[arg-type]
    return await director.resolve(
        turn_index=turn_index,
        turns_left=turns_left,
        user_content="我不同意，这个加班要求不合理",
        opponent_last_reply="这是公司规定",
        trace_id="t1",
        session_id="ses_test",
    )


async def test_first_turn_is_opening_without_llm() -> None:
    llm = _ScriptedLLM("turning")
    arc = await _resolve(llm, turn_index=1, turns_left=29)

    assert arc.stage == "opening"
    assert llm.calls == 0  # deterministic edge, no LLM
    assert "开场" in arc.directive


async def test_second_turn_is_opening() -> None:
    llm = _ScriptedLLM("conflict")
    arc = await _resolve(llm, turn_index=2, turns_left=28)

    assert arc.stage == "opening"
    assert llm.calls == 0


async def test_tail_turns_force_closing_without_llm() -> None:
    """Two turns from the cap → closing, regardless of what the LLM
    might say. The session must wind down, not escalate into a wall."""
    llm = _ScriptedLLM("conflict")
    arc = await _resolve(llm, turn_index=29, turns_left=1)

    assert arc.stage == "closing"
    assert llm.calls == 0
    assert "收尾" in arc.directive


async def test_middle_turn_uses_llm_classification() -> None:
    llm = _ScriptedLLM("turning")
    arc = await _resolve(llm, turn_index=6, turns_left=24)

    assert arc.stage == "turning"
    assert llm.calls == 1
    assert "转折" in arc.directive


async def test_middle_turn_conflict() -> None:
    llm = _ScriptedLLM("conflict")
    arc = await _resolve(llm, turn_index=5, turns_left=25)

    assert arc.stage == "conflict"


async def test_middle_turn_closing_from_llm() -> None:
    """The LLM can move the arc to closing early when the conflict
    resolves, not just at the turn-count tail."""
    llm = _ScriptedLLM("closing")
    arc = await _resolve(llm, turn_index=7, turns_left=23)

    assert arc.stage == "closing"


async def test_llm_response_with_punctuation_is_parsed() -> None:
    llm = _ScriptedLLM("「turning」。")
    arc = await _resolve(llm, turn_index=6, turns_left=24)

    assert arc.stage == "turning"


async def test_unparseable_llm_falls_back_to_conflict() -> None:
    llm = _ScriptedLLM("我觉得这一轮挺激烈的")
    arc = await _resolve(llm, turn_index=6, turns_left=24)

    assert arc.stage == "conflict"


async def test_llm_failure_falls_back_to_conflict() -> None:
    llm = _RaisingLLM()
    arc = await _resolve(llm, turn_index=6, turns_left=24)

    assert arc.stage == "conflict"
    assert llm.calls == 1  # it tried, then fell back


async def test_directive_is_always_populated() -> None:
    """Every stage maps to a non-empty directive — the arbiter relies on
    this being present for any resolved stage."""
    for turn_index, turns_left in [(1, 29), (6, 24), (29, 1)]:
        arc = await _resolve(_ScriptedLLM("conflict"), turn_index=turn_index, turns_left=turns_left)
        assert arc.directive
