"""Behavioural tests for `TurnService` — the per-turn orchestrator.

We don't hit the network. A `_ScriptedProvider` returns canned text
keyed by which system prompt is in front of the messages list, so we
exercise roleplay streaming, coach parsing, and judge parsing in one
deterministic harness.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, datetime

import pytest
from app.agents.state import Verdict
from app.llm import LLMProvider, Message
from app.services.moderation import LogOnlyEventSink, ModerationService, NoopBackend
from app.services.moderation.types import Decision
from app.services.sessions.repository import InMemorySessionRepository, SessionRecord
from app.services.sessions.sse import SseFrame
from app.services.sessions.turn_repository import InMemoryTurnRepository
from app.services.sessions.turn_service import (
    SessionEndedForTurnError,
    SessionNotFoundForTurnError,
    TurnService,
    UserInputBlockedError,
)


class _ScriptedProvider:
    """Picks a response by which system prompt the call carries.

    Roleplay yields in halves so the SSE delta-streaming path is
    exercised; coach + judge are non-streaming consumers in the service.
    """

    name = "scripted"

    def __init__(self, *, roleplay: str, coach: str, judge: str) -> None:
        self._script = {
            "你扮演用户练习对话中的对手": roleplay,
            "你是教练 K": coach,
            "你是评委": judge,
        }

    async def stream_chat(
        self,
        messages: list[Message],
        *,
        temperature: float = 0.7,
        timeout: float = 8.0,
    ) -> AsyncIterator[str]:
        _ = (temperature, timeout)
        system = messages[0].content if messages else ""
        for keyword, response in self._script.items():
            if keyword in system:
                # Two-half yield to drive multi-chunk delta tests.
                yield response[: len(response) // 2]
                yield response[len(response) // 2 :]
                return
        raise AssertionError(f"unscripted system prompt: {system[:60]!r}")


def _moderation(*, block: bool = False) -> ModerationService:
    """`block=True` swaps NoopBackend for one that always blocks."""
    if not block:
        return ModerationService(backend=NoopBackend(), event_sink=LogOnlyEventSink())

    class _BlockingBackend:
        name = "test_block"

        async def evaluate(self, content: str, context: str) -> Decision:
            _ = (content, context)
            return Decision(verdict="block", score=0.99, categories=("other",))

    return ModerationService(backend=_BlockingBackend(), event_sink=LogOnlyEventSink())


def _service(
    *,
    llm_provider: LLMProvider | None = None,
    block_moderation: bool = False,
) -> tuple[TurnService, InMemorySessionRepository, InMemoryTurnRepository]:
    if llm_provider is None:
        llm_provider = _ScriptedProvider(
            roleplay="什么安排比工作还重要？",
            coach="SAFE: 反问 deadline\nAGGRESSIVE: 引用劳动法\nHUMOR: 跟床约了不能放鸽子",
            judge="VERDICT: guolu\nRATING: 70",
        )
    session_repo = InMemorySessionRepository()
    turn_repo = InMemoryTurnRepository()
    svc = TurnService(
        llm=llm_provider,
        moderation=_moderation(block=block_moderation),
        session_repo=session_repo,
        turn_repo=turn_repo,
    )
    return svc, session_repo, turn_repo


def _active_session(scenario_id: str = "sc_001") -> SessionRecord:
    return SessionRecord(
        session_id="ses_aaaa1111",
        user_id="anonymous",
        mode="sandbox",
        scenario_id=scenario_id,
        persona_id="p_hard",
        user_goal="保住周末",
        status="active",
        created_at=datetime(2026, 5, 13, 23, 0, tzinfo=UTC),
    )


async def _collect(stream: AsyncIterator[SseFrame]) -> list[SseFrame]:
    return [f async for f in stream]


# --- validate_turn_request ---


async def test_validate_raises_not_found_for_unknown_session() -> None:
    svc, _, _ = _service()
    with pytest.raises(SessionNotFoundForTurnError):
        await svc.validate_turn_request(
            session_id="ses_never",
            content="hello",
            user_id="anonymous",
            trace_id="t1",
        )


async def test_validate_raises_ended_for_ended_session() -> None:
    svc, session_repo, _ = _service()
    record = _active_session()
    await session_repo.save(SessionRecord(**{**record.__dict__, "status": "ended"}))
    with pytest.raises(SessionEndedForTurnError):
        await svc.validate_turn_request(
            session_id=record.session_id,
            content="hello",
            user_id="anonymous",
            trace_id="t1",
        )


async def test_validate_raises_blocked_when_moderation_rejects() -> None:
    svc, session_repo, _ = _service(block_moderation=True)
    await session_repo.save(_active_session())
    with pytest.raises(UserInputBlockedError):
        await svc.validate_turn_request(
            session_id="ses_aaaa1111",
            content="trash content",
            user_id="anonymous",
            trace_id="t1",
        )


async def test_validate_passes_and_carries_prior_turns_snapshot() -> None:
    svc, session_repo, _ = _service()
    await session_repo.save(_active_session())

    validated = await svc.validate_turn_request(
        session_id="ses_aaaa1111",
        content="老板我周末有事",
        user_id="anonymous",
        trace_id="t1",
    )
    assert validated.session_id == "ses_aaaa1111"
    assert validated.content == "老板我周末有事"
    assert validated.prior_turns == []  # no turns recorded yet


# --- stream_turn ---


async def test_stream_emits_four_event_types_in_order() -> None:
    svc, session_repo, _ = _service()
    await session_repo.save(_active_session())
    validated = await svc.validate_turn_request(
        session_id="ses_aaaa1111",
        content="老板我周末有事",
        user_id="anonymous",
        trace_id="t1",
    )

    frames = await _collect(svc.stream_turn(validated))
    events = [f.event for f in frames]

    # PRD §7.4: at least one opponent.delta, exactly one each of
    # opponent.done / coach.hint / meta, in that strict order.
    assert events.count("opponent.delta") >= 1
    assert events.count("opponent.done") == 1
    assert events.count("coach.hint") == 1
    assert events.count("meta") == 1

    done_idx = events.index("opponent.done")
    coach_idx = events.index("coach.hint")
    meta_idx = events.index("meta")
    assert events.index("opponent.delta") < done_idx
    assert done_idx < coach_idx < meta_idx
    assert meta_idx == len(events) - 1


async def test_opponent_delta_text_concatenates_to_done_full_text() -> None:
    svc, session_repo, _ = _service()
    await session_repo.save(_active_session())
    validated = await svc.validate_turn_request(
        session_id="ses_aaaa1111",
        content="老板我周末有事",
        user_id="anonymous",
        trace_id="t1",
    )

    frames = await _collect(svc.stream_turn(validated))
    delta_text = "".join(f.data["text"] for f in frames if f.event == "opponent.delta")
    done_frame = next(f for f in frames if f.event == "opponent.done")
    assert done_frame.data["turn_id"].startswith("t_")
    assert delta_text == done_frame.data["full_text"]


async def test_coach_hint_frame_has_three_tones() -> None:
    svc, session_repo, _ = _service()
    await session_repo.save(_active_session())
    validated = await svc.validate_turn_request(
        session_id="ses_aaaa1111",
        content="hi",
        user_id="anonymous",
        trace_id="t1",
    )

    frames = await _collect(svc.stream_turn(validated))
    hint = next(f for f in frames if f.event == "coach.hint").data
    assert hint["safe"] == "反问 deadline"
    assert hint["aggressive"] == "引用劳动法"
    assert hint["humor"] == "跟床约了不能放鸽子"


async def test_meta_turns_left_decreases_with_each_turn() -> None:
    svc, session_repo, _ = _service()
    await session_repo.save(_active_session())

    async def one_turn() -> dict[str, object]:
        validated = await svc.validate_turn_request(
            session_id="ses_aaaa1111",
            content="hi",
            user_id="anonymous",
            trace_id="t1",
        )
        frames = await _collect(svc.stream_turn(validated))
        return next(f for f in frames if f.event == "meta").data

    first = await one_turn()
    second = await one_turn()
    assert first["turns_used"] == 1
    assert second["turns_used"] == 2
    # MAX_TURNS_PER_SESSION = 30 → turns_left counts down.
    assert first["turns_left"] == 29
    assert second["turns_left"] == 28


async def test_turn_persisted_after_stream_completes() -> None:
    svc, session_repo, turn_repo = _service()
    await session_repo.save(_active_session())
    validated = await svc.validate_turn_request(
        session_id="ses_aaaa1111",
        content="老板我周末有事",
        user_id="anonymous",
        trace_id="t1",
    )

    await _collect(svc.stream_turn(validated))

    persisted = await turn_repo.list_for_session("ses_aaaa1111")
    assert len(persisted) == 1
    record = persisted[0]
    assert record.user_content == "老板我周末有事"
    assert record.opponent_reply == "什么安排比工作还重要？"
    assert record.coach_hint.safe == "反问 deadline"
    assert record.turn_score.verdict == Verdict.GUOLU
    assert record.turn_score.rating == 70


async def test_coach_parse_failure_falls_back_to_canned_safe_copy() -> None:
    """If the LLM ignores the 3-tone format, the service still emits a
    coach.hint frame with the fallback so the UI never sees an empty hint."""
    svc, session_repo, _ = _service(
        llm_provider=_ScriptedProvider(
            roleplay="某种回应",
            coach="??? not the format ???",
            judge="VERDICT: guolu\nRATING: 50",
        )
    )
    await session_repo.save(_active_session())
    validated = await svc.validate_turn_request(
        session_id="ses_aaaa1111",
        content="hi",
        user_id="anonymous",
        trace_id="t1",
    )

    frames = await _collect(svc.stream_turn(validated))
    hint = next(f for f in frames if f.event == "coach.hint").data
    # All three keys present + non-empty (the canned fallback).
    assert hint["safe"]
    assert hint["aggressive"]
    assert hint["humor"]
