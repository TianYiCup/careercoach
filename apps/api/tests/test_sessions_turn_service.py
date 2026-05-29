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
from app.agents.state import TurnScore, Verdict
from app.llm import LLMProvider, Message, TokenUsage
from app.llm.errors import LLMTimeoutError
from app.services.memory import InMemoryEpisodeRepository, MemoryService
from app.services.moderation import LogOnlyEventSink, ModerationService, NoopBackend
from app.services.moderation.types import Decision
from app.services.profile import InMemoryProfileRepository, ProfileService
from app.services.scenarios.character_vector import CharacterVector
from app.services.sessions.repository import InMemorySessionRepository, SessionRecord
from app.services.sessions.sse import SseFrame
from app.services.sessions.turn_repository import (
    CoachHintTrio,
    InMemoryTurnRepository,
    TurnRecord,
)
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
        usage_sink: list[TokenUsage] | None = None,
    ) -> AsyncIterator[str]:
        _ = (temperature, timeout, usage_sink)
        system = messages[0].content if messages else ""
        for keyword, response in self._script.items():
            if keyword in system:
                # Two-half yield to drive multi-chunk delta tests.
                yield response[: len(response) // 2]
                yield response[len(response) // 2 :]
                return
        raise AssertionError(f"unscripted system prompt: {system[:60]!r}")


class _TimeoutOnRoleplayProvider:
    """Raises LLMTimeoutError before the first roleplay chunk (both
    providers missed the first-byte budget), but answers coach + judge.
    Exercises the L7-era graceful-fallback path so a roleplay timeout
    can't hang the SSE stream."""

    name = "timeout-roleplay"

    async def stream_chat(
        self,
        messages: list[Message],
        *,
        temperature: float = 0.7,
        timeout: float = 8.0,
        usage_sink: list[TokenUsage] | None = None,
    ) -> AsyncIterator[str]:
        _ = (temperature, timeout, usage_sink)
        system = messages[0].content if messages else ""
        if "你扮演用户练习对话中的对手" in system:
            raise LLMTimeoutError("qwen did not produce first chunk within 2.5s", provider="qwen")
        if "你是教练 K" in system:
            yield "SAFE: 稳住\nAGGRESSIVE: 刚回去\nHUMOR: 搞笑一下"
            return
        if "你是评委" in system:
            yield "VERDICT: guolu\nRATING: 50"
            return
        # arc / arbiter / mood prompts — answer empty so they fall back.
        yield ""


def _moderation(
    *,
    block: bool = False,
    verdict: str | None = None,
    output_verdict: str | None = None,
    output_backend_fails: bool = False,
    input_backend_fails: bool = False,
) -> ModerationService:
    """Default returns `allow`. `block=True` short-hand for `verdict='block'`.

    `verdict='warn'` / `'redirect'` lets the A-26 tag tests assert that
    non-allow input verdicts surface on the Langfuse trace tags.

    A-29 adds two output-side options for the same backend:
      * `output_verdict` — override the verdict returned for the
        `ai_output` context (used to test post-roleplay mod gating)
      * `output_backend_fails` — raise ModerationBackendError on the
        `ai_output` call so the silent-allow-but-no-tag path is
        exercised. The input call still succeeds with the normal
        `verdict` so we don't accidentally fail the precheck.

    A-37 adds the symmetric input-side option:
      * `input_backend_fails` — raise ModerationBackendError on the
        `user_input` call to exercise the fail-open path.
    """
    if (
        not block
        and verdict is None
        and output_verdict is None
        and not output_backend_fails
        and not input_backend_fails
    ):
        return ModerationService(backend=NoopBackend(), event_sink=LogOnlyEventSink())

    input_verdict = "block" if block else (verdict or "allow")
    out_verdict = output_verdict if output_verdict is not None else input_verdict

    class _ScriptedBackend:
        name = "test_scripted"

        async def evaluate(self, content: str, context: str) -> Decision:
            _ = content
            if context == "ai_output":
                if output_backend_fails:
                    # ModerationBackendError lives in the backend module;
                    # importing it here keeps the helper self-contained.
                    from app.services.moderation.backend import ModerationBackendError

                    raise ModerationBackendError(
                        "scripted output backend failure",
                        backend="test_scripted",
                    )
                effective = out_verdict
            else:
                if input_backend_fails:
                    from app.services.moderation.backend import ModerationBackendError

                    raise ModerationBackendError(
                        "scripted input backend failure",
                        backend="test_scripted",
                    )
                effective = input_verdict
            categories: tuple[str, ...] = ("other",) if effective != "allow" else ()
            # `redirect` mandates a non-None RedirectResource per the
            # Decision dataclass invariant; supply a placeholder so the
            # test backend can return that verdict without exploding.
            from app.schemas.moderation import RedirectResource

            redirect_resource = (
                RedirectResource(title="test-resource", url="https://example.invalid")
                if effective == "redirect"
                else None
            )
            return Decision(
                verdict=effective,  # type: ignore[arg-type]
                score=0.99,
                categories=categories,  # type: ignore[arg-type]
                redirect_resource=redirect_resource,
            )

    return ModerationService(backend=_ScriptedBackend(), event_sink=LogOnlyEventSink())


def _service(
    *,
    llm_provider: LLMProvider | None = None,
    block_moderation: bool = False,
    moderation_verdict: str | None = None,
    output_moderation_verdict: str | None = None,
    output_moderation_backend_fails: bool = False,
    input_moderation_backend_fails: bool = False,
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
        moderation=_moderation(
            block=block_moderation,
            verdict=moderation_verdict,
            output_verdict=output_moderation_verdict,
            output_backend_fails=output_moderation_backend_fails,
            input_backend_fails=input_moderation_backend_fails,
        ),
        session_repo=session_repo,
        turn_repo=turn_repo,
        langfuse_client=langfuse_client,
        # Fresh profile + memory per service so L5/L6 state stays isolated
        # from the process-wide singletons across tests.
        profile_service=ProfileService(repo=InMemoryProfileRepository()),
        memory_service=MemoryService(repo=InMemoryEpisodeRepository()),
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


_HARSH_MOOD = CharacterVector(
    aggression=85, empathy=20, control=85, honesty=60, stability=30, power_gap=85
)


def _harsh_session() -> SessionRecord:
    """A session whose live mood is a harsh, high-pressure opponent — the
    arbiter falls back to this (scripted provider can't answer the arbiter
    prompt), so next_mood == this and the L7 pressure term is high."""
    record = _active_session()
    return SessionRecord(**{**record.__dict__, "mood_vector": _HARSH_MOOD})


async def _seed_crash_turns(turn_repo: InMemoryTurnRepository, n: int) -> None:
    """Append `n` turns that all ended in 翻车 so the L7 crash-streak signal
    fires."""
    for i in range(n):
        await turn_repo.append(
            TurnRecord(
                turn_id=f"t_crash{i:04d}",
                session_id="ses_aaaa1111",
                user_content="我尽力了",
                opponent_reply="这就是你的水平？",
                coach_hint=CoachHintTrio(safe="稳住", aggressive="刚回去", humor="搞笑"),
                turn_score=TurnScore(verdict=Verdict.FANCHE, rating=20),
                created_at=datetime(2026, 5, 13, 23, 0, tzinfo=UTC),
            )
        )


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


async def test_validate_redirect_carries_resource_onto_validated_turn() -> None:
    """A `redirect` verdict (self-harm) does NOT raise — H-1 streams a
    `moderation` frame, not a 4xx. validate_turn_request returns a
    ValidatedTurn carrying the crisis resource + categories so
    stream_turn can emit them."""
    svc, session_repo, _ = _service(moderation_verdict="redirect")
    await session_repo.save(_active_session())
    validated = await svc.validate_turn_request(
        session_id="ses_aaaa1111",
        content="我不想活了",
        user_id="anonymous",
        trace_id="t1",
    )
    assert validated.input_verdict == "redirect"
    # The crisis resource must survive onto the snapshot — discarding it
    # is exactly the H-1 bug this guards against.
    assert validated.redirect_resource is not None
    assert validated.redirect_resource.title
    assert validated.moderation_categories


async def test_stream_turn_redirect_emits_only_moderation_frame() -> None:
    """PRD §3.0.5 A: a redirect-validated turn force-interrupts the
    practice. stream_turn emits exactly one `moderation` frame with the
    crisis resource and nothing else — no roleplay / coach / meta — and
    persists no turn (the roleplay opponent never replies)."""
    svc, session_repo, turn_repo = _service(moderation_verdict="redirect")
    await session_repo.save(_active_session())
    validated = await svc.validate_turn_request(
        session_id="ses_aaaa1111",
        content="我不想活了",
        user_id="anonymous",
        trace_id="t1",
    )

    frames = await _collect(svc.stream_turn(validated))

    assert [f.event for f in frames] == ["moderation"]
    data = frames[0].data
    assert data["verdict"] == "redirect"
    assert data["redirect_resource"]["title"]
    assert data["redirect_resource"]["url"]
    # No roleplay reply streamed, no turn recorded.
    assert await turn_repo.list_for_session("ses_aaaa1111") == []


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


async def test_stream_emits_arc_then_mood_after_opponent_done() -> None:
    """PR-OPT1: the arc/mood engine runs concurrently with the roleplay
    stream, so arc.update + mood.update now land *after* opponent.done
    (the radar morphs right after the reply, not before the first token).
    arc still precedes mood. Turn 1 → arc is `opening` (deterministic)."""
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

    assert events.count("arc.update") == 1
    done_idx = events.index("opponent.done")
    arc_idx = events.index("arc.update")
    mood_idx = events.index("mood.update")
    assert done_idx < arc_idx < mood_idx

    arc_frame = frames[arc_idx]
    # Turn 1 is always opening (deterministic edge guard).
    assert arc_frame.data["stage"] == "opening"


class _ScriptedWithArbiterProvider:
    """Scripts roleplay / coach / judge AND the merged stage+mood arbiter
    prompt, so the middle-window path (PR-OPT2) can be asserted end-to-end:
    one arbiter call returns both the dramatic stage and the next mood."""

    name = "scripted-arbiter"

    async def stream_chat(
        self,
        messages: list[Message],
        *,
        temperature: float = 0.7,
        timeout: float = 8.0,
        usage_sink: list[TokenUsage] | None = None,
    ) -> AsyncIterator[str]:
        _ = (temperature, timeout, usage_sink)
        system = messages[0].content if messages else ""
        if "你扮演用户练习对话中的对手" in system:
            yield "什么安排比工作还重要？"
        elif "你是教练 K" in system:
            yield "SAFE: a\nAGGRESSIVE: b\nHUMOR: c"
        elif "你是评委" in system:
            yield "VERDICT: shenfeng\nRATING: 85"
        elif "你是对话情绪导演" in system:
            yield (
                "stage: turning\naggression: 60\nempathy: 35\n"
                "control: 78\nhonesty: 55\nstability: 78\npower_gap: 72"
            )
        else:
            raise AssertionError(f"unscripted system prompt: {system[:60]!r}")


async def _seed_neutral_turns(turn_repo: InMemoryTurnRepository, n: int) -> None:
    """Append `n` neutral (路过) turns so the next turn lands in the arc's
    middle window without tripping the L7 crash-streak guard."""
    for i in range(n):
        await turn_repo.append(
            TurnRecord(
                turn_id=f"t_mid{i:04d}",
                session_id="ses_aaaa1111",
                user_content="我再说说我的想法",
                opponent_reply="你接着说",
                coach_hint=CoachHintTrio(safe="稳住", aggressive="刚回去", humor="搞笑"),
                turn_score=TurnScore(verdict=Verdict.GUOLU, rating=60),
                created_at=datetime(2026, 5, 13, 23, 0, tzinfo=UTC),
            )
        )


async def test_middle_window_uses_merged_arc_mood_call() -> None:
    """PR-OPT2: past the opening edge, the arc stage is classified by the
    arbiter's single merged call (not a separate ArcDirector LLM call).
    With 3 prior turns the next turn is turn_index 4 → middle window, so
    the scripted merged response's `stage: turning` surfaces on arc.update
    and its mood on mood.update."""
    svc, session_repo, turn_repo = _service(llm_provider=_ScriptedWithArbiterProvider())
    await session_repo.save(_active_session())
    await _seed_neutral_turns(turn_repo, 3)
    validated = await svc.validate_turn_request(
        session_id="ses_aaaa1111", content="我有个新提议", user_id="anonymous", trace_id="t1"
    )

    frames = await _collect(svc.stream_turn(validated))
    events = [f.event for f in frames]

    arc_frame = frames[events.index("arc.update")]
    assert arc_frame.data["stage"] == "turning"
    mood_frame = frames[events.index("mood.update")]
    # Mood came back populated from the same merged call (clamped to band).
    assert 0 <= mood_frame.data["aggression"] <= 100


async def test_roleplay_llm_timeout_does_not_hang_the_stream() -> None:
    """A roleplay first-byte timeout must NOT crash the SSE stream (which
    leaves the UI stuck on the typing indicator forever). The turn falls
    back to a canned opponent line and still emits opponent.done / coach /
    meta so it terminates cleanly."""
    svc, session_repo, turn_repo = _service(llm_provider=_TimeoutOnRoleplayProvider())
    await session_repo.save(_active_session())
    validated = await svc.validate_turn_request(
        session_id="ses_aaaa1111", content="别废话", user_id="anonymous", trace_id="t1"
    )

    frames = await _collect(svc.stream_turn(validated))
    events = [f.event for f in frames]

    # Stream terminates with the normal terminal frames — no hang.
    assert events.count("opponent.done") == 1
    assert events.count("coach.hint") == 1
    assert events.count("meta") == 1
    # The opponent reply is the graceful fallback, not empty / missing.
    done = next(f for f in frames if f.event == "opponent.done")
    assert done.data["full_text"]
    # The turn was still persisted so history stays consistent.
    assert len(await turn_repo.list_for_session("ses_aaaa1111")) == 1


async def test_safety_minor_softens_after_sustained_crushing() -> None:
    """PR-OPT1 L7: a MINOR + harsh opponent + 3-turn crash streak crosses
    the (stricter) harm threshold → safety.soften fires before the deltas
    (it's a pre-roleplay check), and the opponent's reply this turn uses
    the softened mood — surfaced on the post-reply mood.update."""
    svc, session_repo, turn_repo = _service()
    await session_repo.save(_harsh_session())
    await _seed_crash_turns(turn_repo, 3)
    validated = await svc.validate_turn_request(
        session_id="ses_aaaa1111",
        content="我真的不行了",
        user_id="anonymous",
        is_minor=True,
        trace_id="t1",
    )

    frames = await _collect(svc.stream_turn(validated))
    events = [f.event for f in frames]

    assert events.count("safety.soften") == 1
    safety_idx = events.index("safety.soften")
    first_delta_idx = events.index("opponent.delta")
    assert safety_idx < first_delta_idx  # softens before the reply streams
    assert frames[safety_idx].data["crash_streak"] == 3
    # Minor: the persisted/next mood is softened below the harsh baseline.
    mood_idx = events.index("mood.update")
    assert frames[mood_idx].data["aggression"] < _HARSH_MOOD.aggression


async def test_safety_adult_gets_offramp_not_soften() -> None:
    """PR-OPT1 L7: the SAME crushing as above, but for an ADULT, surfaces
    a safety.offramp (K check-in) — the difficulty is NOT lowered, so the
    opponent keeps its harsh mood and no safety.soften fires."""
    svc, session_repo, turn_repo = _service()
    await session_repo.save(_harsh_session())
    await _seed_crash_turns(turn_repo, 3)
    validated = await svc.validate_turn_request(
        session_id="ses_aaaa1111",
        content="我真的不行了",
        user_id="anonymous",
        is_minor=False,
        trace_id="t1",
    )

    frames = await _collect(svc.stream_turn(validated))
    events = [f.event for f in frames]

    assert events.count("safety.offramp") == 1
    assert events.count("safety.soften") == 0
    offramp_idx = events.index("safety.offramp")
    assert offramp_idx < events.index("opponent.delta")
    assert frames[offramp_idx].data["crash_streak"] == 3


async def test_safety_does_not_fire_in_a_normal_turn() -> None:
    """No crash history + the default neutral session mood → no harm,
    neither soften nor offramp. The guardrail is rare by design."""
    svc, session_repo, _ = _service()
    await session_repo.save(_active_session())
    validated = await svc.validate_turn_request(
        session_id="ses_aaaa1111", content="老板我周末有事", user_id="anonymous", trace_id="t1"
    )

    frames = await _collect(svc.stream_turn(validated))
    events = [f.event for f in frames]
    assert "safety.soften" not in events
    assert "safety.offramp" not in events


async def test_stream_emits_mood_update_after_opponent_done() -> None:
    """PR-OPT1: the arc/mood engine runs concurrently with the reply, so
    mood.update fires once, after opponent.done (radar morphs right after
    the reply). The scripted provider raises on the arbiter prompt, so the
    arbiter falls back to the session's seeded mood — the frame still
    carries a valid 6-dim payload."""
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

    assert events.count("mood.update") == 1
    mood_idx = events.index("mood.update")
    done_idx = events.index("opponent.done")
    assert done_idx < mood_idx

    mood_frame = frames[mood_idx]
    for dim in ("aggression", "empathy", "control", "honesty", "stability", "power_gap"):
        assert dim in mood_frame.data
        assert 0 <= mood_frame.data[dim] <= 100


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


async def test_coach_hint_strategy_null_when_model_omits_it() -> None:
    """PR-L8: the default scripted coach output has no STRATEGY lines, so
    the frame carries strategy=None and the UI drops the card."""
    svc, session_repo, _ = _service()
    await session_repo.save(_active_session())
    validated = await svc.validate_turn_request(
        session_id="ses_aaaa1111", content="hi", user_id="anonymous", trace_id="t1"
    )

    frames = await _collect(svc.stream_turn(validated))
    hint = next(f for f in frames if f.event == "coach.hint").data
    assert hint["strategy"] is None


async def test_coach_hint_carries_strategy_read_when_model_emits_it() -> None:
    """PR-L8: a coach output with the STRATEGY/EFFECT/UPGRADE block lands
    in the frame as a parsed strategy object alongside the three tones."""
    svc, session_repo, _ = _service(
        llm_provider=_ScriptedProvider(
            roleplay="什么安排比工作还重要？",
            coach=(
                "SAFE: 我理解\nAGGRESSIVE: 不合理\nHUMOR: 问菩萨\n"
                "STRATEGY: placate\nEFFECT: poor\nUPGRADE: direct"
            ),
            judge="VERDICT: guolu\nRATING: 70",
        )
    )
    await session_repo.save(_active_session())
    validated = await svc.validate_turn_request(
        session_id="ses_aaaa1111", content="求你了别让我加班", user_id="anonymous", trace_id="t1"
    )

    frames = await _collect(svc.stream_turn(validated))
    hint = next(f for f in frames if f.event == "coach.hint").data
    assert hint["strategy"] == {"strategy": "placate", "effect": "poor", "upgrade": "direct"}
    # The three tones still come through unaffected.
    assert hint["safe"] == "我理解"


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
    # Same event ordering as the un-instrumented tests. PR-OPT1 moved
    # arc/mood after opponent.done, so the tail is coach.hint → meta.
    events = [f.event for f in frames]
    assert events[-2:] == ["coach.hint", "meta"]
    assert "opponent.done" in events


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
        "verdict_input:allow",
    ]

    # Three generations in order: roleplay → coach → judge.
    generation_names = [c.kwargs["name"] for c in inner_trace.generation.call_args_list]
    assert generation_names == ["roleplay", "coach.three_tones", "judge"]

    # Trace finished with the per-turn output payload. A-29 added a
    # second `update(tags=...)` call for `verdict_output:` mid-trace,
    # so we filter to the call that carries the final `output` dict
    # rather than asserting call_count.
    finish_calls = [c for c in inner_trace.update.call_args_list if "output" in c.kwargs]
    assert len(finish_calls) == 1
    finish_kwargs = finish_calls[0].kwargs
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
            usage_sink: list[TokenUsage] | None = None,
        ) -> AsyncIterator[str]:
            _ = (messages, temperature, timeout, usage_sink)
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
        "verdict_input:allow",
    ]


async def test_stream_turn_tags_verdict_warn_when_moderation_warns() -> None:
    """`warn` is the most common non-allow verdict in production — the
    user's text wasn't blocked but tripped the soft-notice tier. The
    trace tag must reflect that so a `verdict_input:warn` Langfuse filter
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
        "verdict_input:warn",
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


# --- A-27: token usage forwarded to Langfuse generations ---


async def test_stream_turn_records_token_usage_when_provider_supplies_it() -> None:
    """A provider that appends to its `usage_sink` (real DeepSeek /
    Qwen do) should have those token counts forwarded to every
    `record_generation` call — roleplay + coach + judge — so the
    Langfuse cost view picks them up on the per-turn span."""
    from unittest.mock import MagicMock

    class _UsageAwareProvider:
        """Yields scripted text AND populates usage_sink, mimicking
        the real adapter contract after A-27."""

        name = "usage_aware"

        def __init__(self) -> None:
            self._script = {
                "你扮演用户练习对话中的对手": "好的",
                "你是教练 K": "SAFE: a\nAGGRESSIVE: b\nHUMOR: c",
                "你是评委": "VERDICT: shenfeng\nRATING: 85",
            }

        async def stream_chat(
            self,
            messages: list[Message],
            *,
            temperature: float = 0.7,
            timeout: float = 8.0,
            usage_sink: list[TokenUsage] | None = None,
        ) -> AsyncIterator[str]:
            _ = (temperature, timeout)
            system = messages[0].content if messages else ""
            for keyword, response in self._script.items():
                if keyword in system:
                    yield response
                    if usage_sink is not None:
                        # Different counts per call so we can tell
                        # which generation received which.
                        usage_sink.append(
                            TokenUsage(
                                prompt_tokens=len(messages),
                                completion_tokens=len(response),
                                total_tokens=len(messages) + len(response),
                            )
                        )
                    return
            raise AssertionError(f"unscripted system prompt: {system[:60]!r}")

    client = MagicMock(name="langfuse")
    inner_trace = MagicMock(name="trace")
    inner_gen = MagicMock(name="generation")
    inner_trace.generation.return_value = inner_gen
    client.trace.return_value = inner_trace

    svc, session_repo, _ = _service(
        llm_provider=_UsageAwareProvider(),
        langfuse_client=client,
    )
    await session_repo.save(_active_session())
    validated = await svc.validate_turn_request(
        session_id="ses_aaaa1111",
        content="hi",
        user_id="u_42",
        trace_id="t1",
    )

    await _collect(svc.stream_turn(validated))

    # Every generation call must carry a `usage` kwarg this time —
    # the provider supplied counts for all three.
    usage_payloads = [
        c.kwargs["usage"] for c in inner_trace.generation.call_args_list if "usage" in c.kwargs
    ]
    assert len(usage_payloads) == 3
    for u in usage_payloads:
        assert set(u.keys()) == {"input", "output", "total"}
        assert u["total"] == u["input"] + u["output"]


async def test_stream_turn_omits_usage_when_provider_doesnt_append() -> None:
    """The default scripted provider doesn't append to usage_sink —
    record_generation must NOT pass `usage=` to Langfuse in that case
    (otherwise the dashboard would see explicit None and clobber any
    backfill the upstream might add later)."""
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
        content="hi",
        user_id="u",
        trace_id="t1",
    )

    await _collect(svc.stream_turn(validated))

    for call in inner_trace.generation.call_args_list:
        assert "usage" not in call.kwargs


# --- A-29: roleplay output moderation + verdict_output trace tag ---


def _verdict_output_tags(trace_mock: object) -> list[str]:
    """Pull the union of `verdict_output:*` tags ever sent via
    `trace.update(tags=...)`. Langfuse v2's update REPLACES, so the
    last call holds the cumulative set."""
    from unittest.mock import MagicMock

    assert isinstance(trace_mock, MagicMock)
    tag_calls = [c.kwargs["tags"] for c in trace_mock.update.call_args_list if "tags" in c.kwargs]
    if not tag_calls:
        return []
    return [t for t in tag_calls[-1] if t.startswith("verdict_output:")]


async def test_output_allow_adds_verdict_output_allow_tag() -> None:
    """The default (NoopBackend default = allow on every context)
    gives `verdict_output:allow` mid-trace via add_tags, alongside the
    initial `verdict_input:allow` set at trace creation."""
    from unittest.mock import MagicMock

    client = MagicMock(name="langfuse")
    inner_trace = MagicMock(name="trace")
    client.trace.return_value = inner_trace

    svc, session_repo, _ = _service(langfuse_client=client)
    await session_repo.save(_active_session())
    validated = await svc.validate_turn_request(
        session_id="ses_aaaa1111",
        content="hi",
        user_id="u",
        trace_id="t1",
    )
    await _collect(svc.stream_turn(validated))

    assert _verdict_output_tags(inner_trace) == ["verdict_output:allow"]


async def test_output_warn_adds_verdict_output_warn_tag() -> None:
    """The roleplay LLM produced text that tripped soft-tier mod —
    we still surface it to the user but tag the trace so an analyst
    can audit AI-side edge content separately from user-side."""
    from unittest.mock import MagicMock

    client = MagicMock(name="langfuse")
    inner_trace = MagicMock(name="trace")
    client.trace.return_value = inner_trace

    svc, session_repo, _ = _service(
        langfuse_client=client,
        output_moderation_verdict="warn",
    )
    await session_repo.save(_active_session())
    validated = await svc.validate_turn_request(
        session_id="ses_aaaa1111",
        content="hi",
        user_id="u",
        trace_id="t1",
    )

    frames = await _collect(svc.stream_turn(validated))

    assert _verdict_output_tags(inner_trace) == ["verdict_output:warn"]
    # `warn` is non-gating: the original LLM text stays in opponent.done.
    done = next(f for f in frames if f.event == "opponent.done")
    assert done.data["full_text"] == "什么安排比工作还重要？"


async def test_output_block_redacts_done_text_and_persisted_record() -> None:
    """`block` from the AI-output mod replaces the authoritative
    `opponent.done.full_text` AND the persisted TurnRecord so the
    bad text doesn't leak into future turns' chat history. The
    `opponent.delta` frames already streamed — we can't unsend
    them — but the trace + done frame + DB row are clean."""
    from unittest.mock import MagicMock

    client = MagicMock(name="langfuse")
    inner_trace = MagicMock(name="trace")
    client.trace.return_value = inner_trace

    svc, session_repo, turn_repo = _service(
        langfuse_client=client,
        output_moderation_verdict="block",
    )
    await session_repo.save(_active_session())
    validated = await svc.validate_turn_request(
        session_id="ses_aaaa1111",
        content="hi",
        user_id="u",
        trace_id="t1",
    )

    frames = await _collect(svc.stream_turn(validated))

    assert _verdict_output_tags(inner_trace) == ["verdict_output:block"]
    done = next(f for f in frames if f.event == "opponent.done")
    assert done.data["full_text"] == "……"
    persisted = await turn_repo.list_for_session("ses_aaaa1111")
    assert persisted[0].opponent_reply == "……"


async def test_output_redirect_also_redacts() -> None:
    """`redirect` from AI-output is rare (the opponent is supposed to
    be adversarial, not in distress) but if the LLM produces e.g.
    self-harm content we treat it like block — replace + tag."""
    from unittest.mock import MagicMock

    client = MagicMock(name="langfuse")
    inner_trace = MagicMock(name="trace")
    client.trace.return_value = inner_trace

    svc, session_repo, _ = _service(
        langfuse_client=client,
        output_moderation_verdict="redirect",
    )
    await session_repo.save(_active_session())
    validated = await svc.validate_turn_request(
        session_id="ses_aaaa1111",
        content="hi",
        user_id="u",
        trace_id="t1",
    )

    frames = await _collect(svc.stream_turn(validated))

    assert _verdict_output_tags(inner_trace) == ["verdict_output:redirect"]
    done = next(f for f in frames if f.event == "opponent.done")
    assert done.data["full_text"] == "……"


async def test_output_backend_failure_tags_backend_failed_sentinel() -> None:
    """When the moderation backend itself fails on the ai_output call
    (e.g. Aliyun outage) we treat-as-allow on the UX side — the
    roleplay text passes through unchanged. A-34 now tags the trace
    with `verdict_output:backend_failed` (the sentinel verdict
    value) so analysts can spot outage windows by filtering the
    same key real verdicts ride."""
    from unittest.mock import MagicMock

    client = MagicMock(name="langfuse")
    inner_trace = MagicMock(name="trace")
    client.trace.return_value = inner_trace

    svc, session_repo, _ = _service(
        langfuse_client=client,
        output_moderation_backend_fails=True,
    )
    await session_repo.save(_active_session())
    validated = await svc.validate_turn_request(
        session_id="ses_aaaa1111",
        content="hi",
        user_id="u",
        trace_id="t1",
    )

    frames = await _collect(svc.stream_turn(validated))

    # Trace surfaces the outage via the shared verdict_output key.
    assert _verdict_output_tags(inner_trace) == ["verdict_output:backend_failed"]
    # Original text passes through unchanged (treat-as-allow UX).
    done = next(f for f in frames if f.event == "opponent.done")
    assert done.data["full_text"] == "什么安排比工作还重要？"


async def test_input_backend_failure_fails_open_and_tags_backend_failed() -> None:
    """A-37: symmetric with the A-34 output-side handling. When the
    moderation backend itself fails on the user_input call (e.g. Aliyun
    is down), `validate_turn_request` MUST NOT raise — that would
    deny service to legit users during ops issues outside their
    control. Instead the turn proceeds and the Langfuse trace gets
    tagged `verdict_input:backend_failed` so the outage window is
    greppable in dashboards."""
    from unittest.mock import MagicMock

    client = MagicMock(name="langfuse")
    inner_trace = MagicMock(name="trace")
    client.trace.return_value = inner_trace

    svc, session_repo, turn_repo = _service(
        langfuse_client=client,
        input_moderation_backend_fails=True,
    )
    await session_repo.save(_active_session())

    # MUST NOT raise — fail-open is the whole point.
    validated = await svc.validate_turn_request(
        session_id="ses_aaaa1111",
        content="hi",
        user_id="u",
        trace_id="t1",
    )
    assert validated.input_verdict == "backend_failed"

    frames = await _collect(svc.stream_turn(validated))

    # Trace tagged at creation time with the sentinel — analysts can
    # filter `verdict_input:backend_failed` to count outage exposure.
    _args, kwargs = client.trace.call_args
    assert "verdict_input:backend_failed" in kwargs["tags"]

    # Roleplay still ran and emitted opponent text (input wasn't blocked).
    done = next(f for f in frames if f.event == "opponent.done")
    assert done.data["full_text"] == "什么安排比工作还重要？"

    # Turn record persisted normally (fail-open lets the practice
    # session continue uninterrupted).
    persisted = await turn_repo.list_for_session("ses_aaaa1111")
    assert len(persisted) == 1


async def test_empty_fallback_reply_skips_output_moderation() -> None:
    """When the LLM hangs up and we emit the `……` fallback, we
    SHOULD NOT pay for a moderation call on placeholder content
    (and we don't want a false `verdict_output:allow` tag for it
    either). The helper short-circuits."""
    from unittest.mock import MagicMock

    class _EmptyProvider:
        name = "empty"

        async def stream_chat(
            self,
            messages: list[Message],
            *,
            temperature: float = 0.7,
            timeout: float = 8.0,
            usage_sink: list[TokenUsage] | None = None,
        ) -> AsyncIterator[str]:
            _ = (messages, temperature, timeout, usage_sink)
            return
            yield ""  # pragma: no cover — makes this an async generator

    client = MagicMock(name="langfuse")
    inner_trace = MagicMock(name="trace")
    client.trace.return_value = inner_trace

    # Provide a different roleplay provider but keep coach + judge
    # paths by wrapping in a multi-script provider would be overkill;
    # the bare _EmptyProvider triggers the fallback for ALL three LLM
    # calls. Coach falls back to canned text; judge fallback raises.
    # We assert only the trace tag here.
    class _MultiEmpty:
        name = "multi_empty"

        async def stream_chat(
            self,
            messages: list[Message],
            *,
            temperature: float = 0.7,
            timeout: float = 8.0,
            usage_sink: list[TokenUsage] | None = None,
        ) -> AsyncIterator[str]:
            _ = (temperature, timeout, usage_sink)
            system = messages[0].content if messages else ""
            if "你扮演用户练习对话中的对手" in system:
                return  # empty roleplay reply triggers `……` fallback
            if "你是教练 K" in system:
                yield "SAFE: a\nAGGRESSIVE: b\nHUMOR: c"
                return
            if "你是评委" in system:
                yield "VERDICT: guolu\nRATING: 50"
                return
            yield ""

    svc, session_repo, _ = _service(
        llm_provider=_MultiEmpty(),
        langfuse_client=client,
    )
    await session_repo.save(_active_session())
    validated = await svc.validate_turn_request(
        session_id="ses_aaaa1111",
        content="hi",
        user_id="u",
        trace_id="t1",
    )

    await _collect(svc.stream_turn(validated))

    # The fallback `……` path skipped output moderation, so no
    # verdict_output tag was ever added.
    assert _verdict_output_tags(inner_trace) == []
