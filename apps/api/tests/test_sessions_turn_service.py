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


def _moderation(
    *,
    block: bool = False,
    verdict: str | None = None,
) -> ModerationService:
    """Default returns `allow`. `block=True` short-hand for `verdict='block'`.

    `verdict='warn'` / `'redirect'` lets the A-26 tag tests assert that
    non-allow input verdicts surface on the Langfuse trace tags.
    """
    if not block and verdict is None:
        return ModerationService(backend=NoopBackend(), event_sink=LogOnlyEventSink())

    effective_verdict = "block" if block else verdict
    categories: tuple[str, ...] = ("other",) if effective_verdict != "allow" else ()

    class _ScriptedBackend:
        name = "test_scripted"

        async def evaluate(self, content: str, context: str) -> Decision:
            _ = (content, context)
            return Decision(
                verdict=effective_verdict,  # type: ignore[arg-type]
                score=0.99,
                categories=categories,  # type: ignore[arg-type]
            )

    return ModerationService(backend=_ScriptedBackend(), event_sink=LogOnlyEventSink())


def _service(
    *,
    llm_provider: LLMProvider | None = None,
    block_moderation: bool = False,
    moderation_verdict: str | None = None,
    langfuse_client: object | None = None,
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
        moderation=_moderation(block=block_moderation, verdict=moderation_verdict),
        session_repo=session_repo,
        turn_repo=turn_repo,
        langfuse_client=langfuse_client,
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


# --- Langfuse trace instrumentation ---


async def test_stream_turn_with_no_langfuse_client_works_unchanged() -> None:
    """The default wiring has no Langfuse keys, so the client is None.
    The stream must still complete end-to-end."""
    svc, session_repo, _ = _service(langfuse_client=None)
    await session_repo.save(_active_session())
    validated = await svc.validate_turn_request(
        session_id="ses_aaaa1111",
        content="hi",
        user_id="anonymous",
        trace_id="t1",
    )

    frames = await _collect(svc.stream_turn(validated))
    # Same event ordering as the un-instrumented tests.
    events = [f.event for f in frames]
    assert events[-3:] == ["opponent.done", "coach.hint", "meta"]


async def test_stream_turn_emits_trace_with_three_generations() -> None:
    """When Langfuse is wired, one trace per turn + one generation per
    LLM call (roleplay / coach / judge)."""
    from unittest.mock import MagicMock

    client = MagicMock(name="langfuse")
    inner_trace = MagicMock(name="trace")
    inner_gen = MagicMock(name="generation")
    inner_trace.generation.return_value = inner_gen
    client.trace.return_value = inner_trace

    svc, session_repo, _ = _service(langfuse_client=client)
    await session_repo.save(_active_session())
    validated = await svc.validate_turn_request(
        session_id="ses_aaaa1111",
        content="周末有事",
        user_id="u_42",
        trace_id="trace-from-route",
    )

    await _collect(svc.stream_turn(validated))

    # One top-level trace, with the input + metadata + session_id the
    # route fed in. `session_id` is the Langfuse top-level grouping
    # field (A-23) — used to be in `input`.
    client.trace.assert_called_once()
    _args, kwargs = client.trace.call_args
    assert kwargs["name"] == "session_turn"
    assert kwargs["session_id"] == "ses_aaaa1111"
    assert kwargs["input"]["user_content"] == "周末有事"
    assert kwargs["input"]["prior_turn_count"] == 0
    assert kwargs["metadata"]["user_id"] == "u_42"
    assert kwargs["metadata"]["trace_id"] == "trace-from-route"
    assert kwargs["metadata"]["scenario_id"] == "sc_001"
    # A-26: sandbox baseline tags — adult user, allow verdict from NoopBackend.
    assert kwargs["tags"] == [
        "surface:sandbox",
        "minor:false",
        "verdict:allow",
    ]

    # Three generations in order: roleplay → coach → judge.
    generation_names = [c.kwargs["name"] for c in inner_trace.generation.call_args_list]
    assert generation_names == ["roleplay", "coach.three_tones", "judge"]

    # Trace finished with the per-turn output payload.
    inner_trace.update.assert_called_once()
    finish_kwargs = inner_trace.update.call_args.kwargs
    assert "output" in finish_kwargs
    output = finish_kwargs["output"]
    assert output["verdict"] == "guolu"
    assert output["rating"] == 70
    assert output["turns_used"] == 1
    assert output["turn_id"].startswith("t_")


async def test_stream_turn_marks_trace_error_when_llm_blows_up() -> None:
    """Exception during the SSE pipeline must be captured on the trace
    AND re-raised — the route's error handler still needs to see it."""
    from unittest.mock import MagicMock

    class _BoomProvider:
        name = "boom"

        async def stream_chat(
            self,
            messages: list[Message],
            *,
            temperature: float = 0.7,
            timeout: float = 8.0,
        ) -> AsyncIterator[str]:
            _ = (messages, temperature, timeout)
            raise RuntimeError("scripted provider failure")
            yield ""  # pragma: no cover — unreachable

    client = MagicMock(name="langfuse")
    inner_trace = MagicMock(name="trace")
    client.trace.return_value = inner_trace

    svc, session_repo, _ = _service(
        llm_provider=_BoomProvider(),
        langfuse_client=client,
    )
    await session_repo.save(_active_session())
    validated = await svc.validate_turn_request(
        session_id="ses_aaaa1111",
        content="hi",
        user_id="anonymous",
        trace_id="t1",
    )

    with pytest.raises(RuntimeError, match="scripted provider failure"):
        await _collect(svc.stream_turn(validated))

    # Trace marked ERROR with the exception message.
    inner_trace.update.assert_called_once()
    fail_kwargs = inner_trace.update.call_args.kwargs
    assert fail_kwargs.get("level") == "ERROR"
    assert "scripted provider failure" in fail_kwargs.get("status_message", "")


# --- A-26: minor + verdict trace tags ---


async def test_stream_turn_tags_minor_true_when_user_is_under_18() -> None:
    """`is_minor=True` from the JWT must surface as `minor:true` on the
    sandbox trace so analysts can filter PRD §3.0.5 C strict-tier
    traffic without grepping metadata."""
    from unittest.mock import MagicMock

    client = MagicMock(name="langfuse")
    inner_trace = MagicMock(name="trace")
    client.trace.return_value = inner_trace

    svc, session_repo, _ = _service(langfuse_client=client)
    await session_repo.save(_active_session())
    validated = await svc.validate_turn_request(
        session_id="ses_aaaa1111",
        content="hi",
        user_id="u_minor",
        is_minor=True,
        trace_id="t1",
    )

    await _collect(svc.stream_turn(validated))

    _args, kwargs = client.trace.call_args
    assert kwargs["tags"] == [
        "surface:sandbox",
        "minor:true",
        "verdict:allow",
    ]


async def test_stream_turn_tags_verdict_warn_when_moderation_warns() -> None:
    """`warn` is the most common non-allow verdict in production — the
    user's text wasn't blocked but tripped the soft-notice tier. The
    trace tag must reflect that so a `verdict:warn` Langfuse filter
    surfaces these for review."""
    from unittest.mock import MagicMock

    client = MagicMock(name="langfuse")
    inner_trace = MagicMock(name="trace")
    client.trace.return_value = inner_trace

    svc, session_repo, _ = _service(
        langfuse_client=client,
        moderation_verdict="warn",
    )
    await session_repo.save(_active_session())
    validated = await svc.validate_turn_request(
        session_id="ses_aaaa1111",
        content="边缘内容",
        user_id="anonymous",
        trace_id="t1",
    )

    await _collect(svc.stream_turn(validated))

    _args, kwargs = client.trace.call_args
    assert kwargs["tags"] == [
        "surface:sandbox",
        "minor:false",
        "verdict:warn",
    ]


async def test_validated_turn_carries_moderation_context() -> None:
    """`validate_turn_request` must propagate is_minor + verdict onto
    ValidatedTurn so any downstream consumer (not just stream_turn)
    can use them without re-running moderation.

    Uses adult + warn rather than minor + warn because the moderation
    service's minor-strictness rule upgrades `warn` → `block` for
    under-18s (PRD §3.0.5 C). The plumbing assertion is independent
    of that gate — we just need a verdict that survives validation.
    """
    svc, session_repo, _ = _service(moderation_verdict="warn")
    await session_repo.save(_active_session())

    validated = await svc.validate_turn_request(
        session_id="ses_aaaa1111",
        content="边缘但不阻断的内容",
        user_id="u_x",
        is_minor=False,
        trace_id="t1",
    )

    assert validated.is_minor is False
    assert validated.input_verdict == "warn"
