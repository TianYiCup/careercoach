"""`TurnService` — orchestrates one turn end-to-end with SSE streaming.

PR 4b flow for `POST /v1/sessions/{id}/turns`:

    request  → service.validate_turn_request(...)              # raises before stream opens
              ↳ session lookup, status check, moderation gate
    response → StreamingResponse(service.stream_turn(...))
              ↳ roleplay LLM stream → opponent.delta frames
              ↳ opponent.done frame (turn_id + full_text)
              ↳ coach (single LLM call, parse into 3 tones)
                → coach.hint frame
              ↳ judge (single LLM call, parse VERDICT + RATING)
                → TurnScore persisted via TurnRepository
              ↳ meta frame (turns_used, turns_left)
    persist → TurnRepository.append(...)

Why not run the LangGraph orchestrator here? The compiled graph in
`app.agents.orchestrator.build_graph` is convenient when each node
just runs to completion, but SSE needs *token-level* streaming from
the roleplay node. Driving the LLM directly here is simpler than
plumbing `astream_events` through three nodes for a single PR. The
graph stays in place for future cycle/checkpoint work.

PR 4c will swap the per-turn TurnScore output for an aggregator that
synthesises the 5-dim Score on `/end`. Until then `/end` still emits
the stub Score from PR 4a.
"""

from __future__ import annotations

import re
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime

import structlog
from langfuse import Langfuse

from app.agents.judge import parse_judge_output
from app.agents.state import TurnScore
from app.llm import LLMProvider, Message, TokenUsage
from app.observability.langfuse import TurnTrace, begin_turn_trace
from app.schemas.moderation import ModerationCheckRequest
from app.services.moderation.service import ModerationService
from app.services.sessions.repository import SessionRepository
from app.services.sessions.scenario_seed import get_scenario_seed
from app.services.sessions.sse import SseFrame
from app.services.sessions.turn_repository import (
    CoachHintTrio,
    TurnRecord,
    TurnRepository,
)

logger = structlog.get_logger(__name__)

MAX_TURNS_PER_SESSION = 30
"""PRD §7.4 — soft cap per sandbox session. Surfaced in `meta.turns_left`."""


# Same prompts as the agents package, kept local so this service can
# evolve them independently of the LangGraph node body. When the two
# converge in a later sprint, we'll point both at one module.
_ROLEPLAY_PROMPT = (
    "你扮演用户练习对话中的对手，回应要自然、不超过 80 字，不要给建议，也不要破坏角色。"
)
_COACH_PROMPT = (
    "你是教练 K。看完对手的回应，给用户三档下一句提示，"
    "每行 ≤ 30 字，按以下格式严格输出，不要解释、不要任何额外文字：\n"
    "SAFE: <稳如老狗版>\n"
    "AGGRESSIVE: <正面刚版>\n"
    "HUMOR: <整活版>"
)
_JUDGE_PROMPT = (
    "你是评委。看完用户与对手的对话，对【用户的话】给一个评分。\n"
    "只输出两行：\n"
    "VERDICT: shenfeng | guolu | fanche\n"
    "RATING: 0-100 的整数\n"
    "不要解释，不要任何额外文字。"
)

_COACH_SAFE_RE = re.compile(r"SAFE\s*:\s*(.+)", re.IGNORECASE)
_COACH_AGGRO_RE = re.compile(r"AGGRESSIVE\s*:\s*(.+)", re.IGNORECASE)
_COACH_HUMOR_RE = re.compile(r"HUMOR\s*:\s*(.+)", re.IGNORECASE)

_COACH_FALLBACK = CoachHintTrio(
    safe="先稳住，反问对方真正的诉求",
    aggressive="直接指出底线，不退让",
    humor="用一句玩笑把球踢回去",
)


class SessionNotFoundForTurnError(LookupError):
    """Route maps to 404."""


class SessionEndedForTurnError(RuntimeError):
    """Route maps to 409 — can't add turns to an ended session."""


class UserInputBlockedError(RuntimeError):
    """Route maps to 400 — moderation rejected the content."""

    def __init__(self, *, categories: tuple[str, ...]) -> None:
        super().__init__(f"user input blocked by moderation: {categories}")
        self.categories = categories


class ValidatedTurn:
    """Snapshot the route hands to `stream_turn` after `validate_turn_request`.

    Carrying it explicitly forces the route to call validate first and
    surface 4xx errors as real HTTP responses — never via mid-stream
    SSE error frames, which clients handle inconsistently.
    """

    __slots__ = (
        "content",
        "input_verdict",
        "is_minor",
        "prior_turns",
        "session_id",
        "trace_id",
        "user_id",
    )

    def __init__(
        self,
        *,
        session_id: str,
        content: str,
        user_id: str,
        trace_id: str,
        prior_turns: list[TurnRecord],
        is_minor: bool = False,
        input_verdict: str = "allow",
    ) -> None:
        self.session_id = session_id
        self.content = content
        self.user_id = user_id
        self.trace_id = trace_id
        self.prior_turns = prior_turns
        # A-26: carried out of validate_turn_request so stream_turn can
        # tag the Langfuse trace without re-running moderation. Default
        # values keep older test harnesses that build ValidatedTurn
        # directly from passing.
        self.is_minor = is_minor
        self.input_verdict = input_verdict


class TurnService:
    """One method per phase so route + tests can lean on each in isolation."""

    def __init__(
        self,
        *,
        llm: LLMProvider,
        moderation: ModerationService,
        session_repo: SessionRepository,
        turn_repo: TurnRepository,
        langfuse_client: Langfuse | None = None,
    ) -> None:
        self._llm = llm
        self._moderation = moderation
        self._session_repo = session_repo
        self._turn_repo = turn_repo
        # `None` is a real, supported value — when Langfuse keys aren't
        # configured `begin_turn_trace` returns a no-op `TurnTrace` so
        # `stream_turn` never branches on observability state.
        self._langfuse_client = langfuse_client

    async def validate_turn_request(
        self,
        *,
        session_id: str,
        content: str,
        user_id: str,
        is_minor: bool = False,
        trace_id: str,
    ) -> ValidatedTurn:
        """Run all checks that should fail with 4xx instead of an SSE error.

        `is_minor` flows from the JWT through the route layer so the
        moderation strict tier (PRD §3.0.5 C) fires for under-18 users.
        Default False keeps the historical behavior for any test that
        hasn't been updated yet — production routes always pass the
        JWT-derived value.
        """
        session = await self._session_repo.get(session_id)
        if session is None:
            raise SessionNotFoundForTurnError(session_id)
        if session.status != "active":
            raise SessionEndedForTurnError(session_id)

        decision = await self._moderation.check(
            ModerationCheckRequest(
                content=content,
                context="user_input",
                session_id=session_id,
            ),
            user_id=user_id,
            is_minor=is_minor,
            trace_id=trace_id,
        )
        if decision.verdict == "block":
            logger.info(
                "turn_user_input_blocked",
                session_id=session_id,
                trace_id=trace_id,
                categories=list(decision.categories),
            )
            raise UserInputBlockedError(categories=tuple(decision.categories))

        prior_turns = await self._turn_repo.list_for_session(session_id)
        return ValidatedTurn(
            session_id=session_id,
            content=content,
            user_id=user_id,
            trace_id=trace_id,
            prior_turns=prior_turns,
            is_minor=is_minor,
            input_verdict=decision.verdict,
        )

    async def stream_turn(self, validated: ValidatedTurn) -> AsyncIterator[SseFrame]:
        """Drive the LLM, emit SSE frames, persist the turn record.

        Wrapped in a Langfuse `session_turn` trace. Each LLM call inside
        emits a `generation` span; the trace is finished with the
        per-turn output payload, or marked `level=ERROR` on exception
        before re-raising. When `LANGFUSE_*` keys aren't configured
        the trace is a no-op so dev runs aren't slowed down.
        """
        session = await self._session_repo.get(validated.session_id)
        # `validate_turn_request` already confirmed the session exists; the
        # repo lookup here is just to grab the scenario_id for prompts.
        assert session is not None  # pragma: no cover — guarded by precheck

        trace = begin_turn_trace(
            self._langfuse_client,
            input={
                "user_content": validated.content,
                "prior_turn_count": len(validated.prior_turns),
            },
            # `session_id` is promoted to Langfuse's top-level session
            # field (A-23) so every turn in a sandbox session lands
            # under the same Langfuse row. `trace_id` stays in
            # metadata as the per-request request-id correlator.
            metadata={
                "user_id": validated.user_id,
                "trace_id": validated.trace_id,
                "scenario_id": session.scenario_id,
            },
            session_id=validated.session_id,
            # A-24/A-26: cross-cutting Langfuse filter tags.
            #   surface:sandbox  — which of the three surfaces this is
            #   minor:{t|f}      — strict-tier moderation was applied
            #   verdict:{...}    — moderation decision on user input
            #
            # Sandbox uses a single `verdict:` key (matching copilot)
            # because only the user input is moderated. Review uses
            # `verdict_input:` + `verdict_output:` because it moderates
            # both. Mixing the keys would let an analyst filter
            # `verdict:block` and see review traces where the LLM
            # output was bad mixed with sandbox traces where the
            # user text was bad.
            tags=[
                "surface:sandbox",
                f"minor:{'true' if validated.is_minor else 'false'}",
                f"verdict:{validated.input_verdict}",
            ],
        )

        try:
            seed = get_scenario_seed(session.scenario_id)
            history = _build_history(
                seed_opening=seed.opening_line,
                prior_turns=validated.prior_turns,
            )

            roleplay_messages: list[Message] = [
                Message.system(_ROLEPLAY_PROMPT),
                *history,
                Message.user(validated.content),
            ]

            roleplay_usage: list[TokenUsage] = []
            full_reply_parts: list[str] = []
            async for chunk in self._llm.stream_chat(roleplay_messages, usage_sink=roleplay_usage):
                if not chunk:
                    continue
                full_reply_parts.append(chunk)
                yield SseFrame("opponent.delta", {"text": chunk})

            full_reply = "".join(full_reply_parts).strip()
            if not full_reply:
                # LLM hung up before producing anything — emit a graceful
                # fallback line so the UI doesn't render an empty bubble.
                full_reply = "……"
                yield SseFrame("opponent.delta", {"text": full_reply})

            trace.record_generation(
                name="roleplay",
                model=self._llm.name,
                input=[m.model_dump() for m in roleplay_messages],
                output=full_reply,
                usage=roleplay_usage[0] if roleplay_usage else None,
            )

            turn_id = _new_turn_id()
            yield SseFrame(
                "opponent.done",
                {"turn_id": turn_id, "full_text": full_reply},
            )

            coach_hint = await self._coach_three_tones(validated.content, full_reply, trace)
            yield SseFrame(
                "coach.hint",
                {
                    "safe": coach_hint.safe,
                    "aggressive": coach_hint.aggressive,
                    "humor": coach_hint.humor,
                },
            )

            turn_score = await self._judge_turn(validated.content, full_reply, trace)

            record = TurnRecord(
                turn_id=turn_id,
                session_id=validated.session_id,
                user_content=validated.content,
                opponent_reply=full_reply,
                coach_hint=coach_hint,
                turn_score=turn_score,
                created_at=datetime.now(UTC),
            )
            await self._turn_repo.append(record)

            turns_used = len(validated.prior_turns) + 1
            yield SseFrame(
                "meta",
                {
                    "turns_used": turns_used,
                    "turns_left": max(0, MAX_TURNS_PER_SESSION - turns_used),
                },
            )

            logger.info(
                "turn_completed",
                session_id=validated.session_id,
                turn_id=turn_id,
                turns_used=turns_used,
                verdict=turn_score.verdict,
                rating=turn_score.rating,
            )
            trace.finish(
                output={
                    "turn_id": turn_id,
                    "opponent_reply": full_reply,
                    "verdict": turn_score.verdict,
                    "rating": turn_score.rating,
                    "turns_used": turns_used,
                }
            )
        except Exception as exc:
            # GeneratorExit (client disconnect) is BaseException, not
            # Exception — it falls through this handler unmarked so we
            # don't paint normal cancellation as a server failure on
            # the Langfuse UI.
            trace.fail(exc)
            raise

    async def _coach_three_tones(
        self,
        user_content: str,
        opponent_reply: str,
        trace: TurnTrace,
    ) -> CoachHintTrio:
        """Single LLM call → parse the three-tone block. Fallback on parse fail."""
        prompt = f"用户刚说：{user_content}\n对手回应：{opponent_reply}\n请按三档输出。"
        messages = [Message.system(_COACH_PROMPT), Message.user(prompt)]
        usage: list[TokenUsage] = []
        raw = await _collect(self._llm.stream_chat(messages, usage_sink=usage))
        trace.record_generation(
            name="coach.three_tones",
            model=self._llm.name,
            input=[m.model_dump() for m in messages],
            output=raw,
            usage=usage[0] if usage else None,
        )
        return _parse_three_tones(raw)

    async def _judge_turn(
        self,
        user_content: str,
        opponent_reply: str,
        trace: TurnTrace,
    ) -> TurnScore:
        """Reuse the agent-level parser so SSE + LangGraph stay in sync."""
        prompt = f"用户的话：{user_content}\n对手的回应：{opponent_reply}\n请评分。"
        messages = [Message.system(_JUDGE_PROMPT), Message.user(prompt)]
        usage: list[TokenUsage] = []
        raw = await _collect(self._llm.stream_chat(messages, usage_sink=usage))
        trace.record_generation(
            name="judge",
            model=self._llm.name,
            input=[m.model_dump() for m in messages],
            output=raw,
            usage=usage[0] if usage else None,
        )
        return parse_judge_output(raw)


def _build_history(
    *,
    seed_opening: str,
    prior_turns: list[TurnRecord],
) -> list[Message]:
    """Reconstruct the chat history seen by the roleplay LLM.

    The scenario's opening line counts as the opponent's first turn so
    the LLM has a stance to react against. Each subsequent turn adds a
    user message and the opponent reply that came back.
    """
    history: list[Message] = [Message.assistant(seed_opening)]
    for turn in prior_turns:
        history.append(Message.user(turn.user_content))
        history.append(Message.assistant(turn.opponent_reply))
    return history


def _parse_three_tones(raw: str) -> CoachHintTrio:
    """Best-effort parse; missing fields fall back to canned safe copy."""
    safe = _COACH_SAFE_RE.search(raw)
    aggro = _COACH_AGGRO_RE.search(raw)
    humor = _COACH_HUMOR_RE.search(raw)
    if not (safe and aggro and humor):
        logger.warning("coach_unparseable", raw=raw[:200])
        return _COACH_FALLBACK
    return CoachHintTrio(
        safe=safe.group(1).strip(),
        aggressive=aggro.group(1).strip(),
        humor=humor.group(1).strip(),
    )


async def _collect(stream: AsyncIterator[str]) -> str:
    """Drain an async stream into one string. Coach + judge are
    non-streaming consumers, so we collect rather than yield."""
    parts: list[str] = []
    async for chunk in stream:
        parts.append(chunk)
    return "".join(parts)


def _new_turn_id() -> str:
    """`t_<8-hex>` matching the MSW mock + SSE schema example."""
    return f"t_{uuid.uuid4().hex[:8]}"


__all__ = [
    "MAX_TURNS_PER_SESSION",
    "SessionEndedForTurnError",
    "SessionNotFoundForTurnError",
    "TurnService",
    "UserInputBlockedError",
    "ValidatedTurn",
]
