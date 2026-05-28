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
from dataclasses import dataclass, replace
from datetime import UTC, datetime

import structlog
from langfuse import Langfuse

from app.agents.judge import parse_judge_output
from app.agents.state import TurnScore
from app.llm import LLMProvider, Message, TokenUsage
from app.observability.langfuse import TurnTrace, begin_turn_trace
from app.schemas.moderation import ModerationCheckRequest, RedirectResource
from app.services.memory import MemoryService, build_memory_note, get_memory_service
from app.services.moderation import ModerationBackendError
from app.services.moderation.service import ModerationService
from app.services.profile import ProfileService, get_profile_service
from app.services.scenarios.character_vector import (
    describe_for_coach,
    describe_for_roleplay,
)
from app.services.sessions.arc_director import ArcDirector
from app.services.sessions.coach_strategy import (
    STRATEGY_PROMPT_BLOCK,
    CoachStrategyRead,
    parse_strategy_read,
)
from app.services.sessions.mood_arbiter import MoodArbiter
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


@dataclass(frozen=True)
class CoachResult:
    """What the single coach LLM call yields (PR-L8): the persisted
    three-tone hint trio plus the optional strategy read. `strategy` is
    None when the model didn't emit a parseable on-vocabulary read —
    the caller then omits the strategy card."""

    hints: CoachHintTrio
    strategy: CoachStrategyRead | None


# Same prompts as the agents package, kept local so this service can
# evolve them independently of the LangGraph node body. When the two
# converge in a later sprint, we'll point both at one module.
#
# PR-D4: prompts are now BUILDERS that take the live session context.
# The previous constants didn't know the scenario title, the user's
# goal, or which side of the conversation the user was on — the LLM
# drifted to weather chitchat in the roleplay node, and the coach
# produced lines from the opponent's POV instead of the user's.


def _build_roleplay_prompt(
    *,
    scenario_title: str,
    background: str,
    persona_title: str,
    user_goal: str,
    character_descriptor: str = "",
    memory_note: str = "",
) -> str:
    """System prompt for the AI opponent.

    Pins the LLM to the scenario, the persona, and — crucially — to
    the opposing side of the user's stated goal. Without the explicit
    "你在跟用户对立面" line, models defaulted to neutral friendly
    chitchat and forgot they were a tough negotiation counterpart.

    PR-L1.3: `character_descriptor` carries the 6-dim persona profile
    rendered as a Chinese bullet list (see
    `app.services.scenarios.character_vector.describe_for_roleplay`).
    When the vector is at the neutral baseline the descriptor is empty
    and the prompt collapses to the previous shape — so an unmigrated
    custom scenario still works end-to-end.

    PR-L6: `memory_note` carries the opponent's recall of past sessions
    in this scenario ("你之前和这个用户交手过 N 次..."). Empty on a
    first visit, so the prompt is unchanged for new (user, scenario)
    pairs."""
    descriptor_block = f"\n{character_descriptor}\n\n" if character_descriptor else ""
    memory_block = f"\n{memory_note}\n" if memory_note else ""
    return (
        f"你扮演用户练习对话中的对手。场景：「{scenario_title}」。\n"
        f"场景背景：{background}\n"
        f"你的角色身份：{persona_title}。\n"
        f"用户的目标是：{user_goal}\n"
        f"{descriptor_block}"
        f"{memory_block}"
        "你站在与用户对立的一方，要让用户感受到压力，但不能爆粗、不能人身攻击。\n"
        "回应要自然、像真人说话，不超过 80 字。不要给用户建议，不要破坏角色，不要替用户说话。"
    )


def _build_coach_prompt(
    *,
    scenario_title: str,
    user_goal: str,
    opponent_profile: str = "",
) -> str:
    """System prompt for教练 K's three-tone hint.

    PR-D4: K used to drift into the opponent's voice ("我打游戏关你什么事"
    in the 室友打游戏 scenario, said as if the user *was* the gamer
    instead of the one losing sleep). Pinning the user's side via
    user_goal in the system prompt fixes the perspective.

    PR-L1.3: `opponent_profile` (3-dim subset rendered by
    `describe_for_coach`) lets K tune the hint to who the user is up
    against —硬顶 a high-power_gap boss vs the same line to a peer
    are very different advice. Empty when no dim is in the outer band
    (e.g. fallback record); prompt then reads as before."""
    opponent_block = f"\n{opponent_profile}\n\n" if opponent_profile else ""
    return (
        f"你是教练 K，正在指导【用户】练习对话。\n"
        f"场景：「{scenario_title}」\n"
        f"用户的目标：{user_goal}\n"
        f"{opponent_block}"
        "你站在【用户这一边】，给出的提示是【用户接下来要说的话】，不是对手的话，"
        "也不是替用户分析对方。每行直接是用户可以照说的一句话。\n"
        "看完对手的回应，给用户三档下一句提示，每行 ≤ 30 字，按以下格式严格输出，"
        "不要解释、不要任何额外文字：\n"
        "SAFE: <稳如老狗版>\n"
        "AGGRESSIVE: <正面刚版>\n"
        "HUMOR: <整活版>\n"
        # PR-L8: strategy read appended after the three tones.
        f"{STRATEGY_PROMPT_BLOCK}"
    )


_JUDGE_PROMPT = (
    "你是评委。看完用户与对手的对话，对【用户的话】给一个评分。\n"
    "只输出两行：\n"
    "VERDICT: shenfeng | guolu | fanche\n"
    "RATING: 0-100 的整数\n"
    "不要解释，不要任何额外文字。"
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

# Used when post-stream output moderation gates the roleplay LLM's
# reply. The user already saw the deltas (they were streamed live);
# this replaces only the authoritative `opponent.done.full_text` +
# the persisted turn record, so chat history doesn't carry the bad
# text into future turns. Frontend can render it as "[opponent fell
# silent]" or similar based on the verdict_output trace tag.
_ROLEPLAY_REDACTED_PLACEHOLDER = "……"


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
        "moderation_categories",
        "prior_turns",
        "redirect_resource",
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
        redirect_resource: RedirectResource | None = None,
        moderation_categories: tuple[str, ...] = (),
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
        # H-1: only populated when input_verdict == "redirect" — the
        # crisis resource + categories stream_turn emits as the single
        # `moderation` frame (PRD §3.0.5 A). Empty for every other path.
        self.redirect_resource = redirect_resource
        self.moderation_categories = moderation_categories


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
        mood_arbiter: MoodArbiter | None = None,
        arc_director: ArcDirector | None = None,
        profile_service: ProfileService | None = None,
        memory_service: MemoryService | None = None,
    ) -> None:
        self._llm = llm
        self._moderation = moderation
        self._session_repo = session_repo
        self._turn_repo = turn_repo
        # PR-L3: defaults to a MoodArbiter over the same LLM. Injectable
        # so tests can pass a scripted arbiter (or None to disable the
        # mood frame entirely on the pre-L3 paths).
        self._mood_arbiter = mood_arbiter if mood_arbiter is not None else MoodArbiter(llm)
        # PR-L2: arc director shapes the dramatic stage that biases the
        # arbiter. Same default-over-llm + injectable pattern.
        self._arc_director = arc_director if arc_director is not None else ArcDirector(llm)
        # PR-L5: records each coach strategy read into the user profile
        # so the opponent's intensity adapts over time. Shared singleton
        # so the session-create adaptation sees the same data.
        self._profile = profile_service if profile_service is not None else get_profile_service()
        # PR-L6: recalls past (user, scenario) episodes so the opponent's
        # roleplay prompt carries a "你之前交手过 N 次" memory note.
        self._memory = memory_service if memory_service is not None else get_memory_service()
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

        # H-1: a `redirect` verdict carries a crisis resource that
        # stream_turn surfaces as a `moderation` frame. Default empty
        # for the common allow / warn / backend_failed paths.
        redirect_resource: RedirectResource | None = None
        moderation_categories: tuple[str, ...] = ()

        try:
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
        except ModerationBackendError as exc:
            # A-37: fail-open mirror of A-34's output-side handling. The
            # moderation backend (Aliyun + local-dict cascade) was down
            # for THIS request, so we'd either have to 5xx the user
            # (worst UX during an outage that's already not their
            # fault) or let the content through unscored. We pick the
            # latter and rely on the `verdict_input:backend_failed`
            # Langfuse tag so analysts can count exposure during a
            # mod-backend incident without scraping logs.
            #
            # Why not block-by-default: PRD §3.0.5 reserves block for
            # the six red lines; treating a transport outage as a red-
            # line hit would distort the audit signal and would also
            # leave legit users unable to practice during ops issues
            # entirely outside their control. Output-side moderation
            # (A-29) still runs on the roleplay reply.
            logger.warning(
                "turn_input_moderation_unavailable",
                session_id=session_id,
                trace_id=trace_id,
                backend=exc.backend,
                error=str(exc),
            )
            input_verdict = "backend_failed"
        else:
            if decision.verdict == "block":
                logger.info(
                    "turn_user_input_blocked",
                    session_id=session_id,
                    trace_id=trace_id,
                    categories=list(decision.categories),
                )
                raise UserInputBlockedError(categories=tuple(decision.categories))
            input_verdict = decision.verdict
            if decision.verdict == "redirect":
                # H-1 / PRD §3.0.5 A: a `redirect` verdict (self-harm /
                # crisis) does NOT 4xx and does NOT run roleplay — the
                # turn streams a single `moderation` frame instead.
                # Carry the crisis resource + categories so stream_turn
                # can emit them. The hit is already audited to the
                # ModerationEvent log inside `_moderation.check`.
                redirect_resource = decision.redirect_resource
                moderation_categories = tuple(decision.categories)

        prior_turns = await self._turn_repo.list_for_session(session_id)
        return ValidatedTurn(
            session_id=session_id,
            content=content,
            user_id=user_id,
            trace_id=trace_id,
            prior_turns=prior_turns,
            is_minor=is_minor,
            input_verdict=input_verdict,
            redirect_resource=redirect_resource,
            moderation_categories=moderation_categories,
        )

    async def stream_turn(self, validated: ValidatedTurn) -> AsyncIterator[SseFrame]:
        """Drive the LLM, emit SSE frames, persist the turn record.

        Wrapped in a Langfuse `session_turn` trace. Each LLM call inside
        emits a `generation` span; the trace is finished with the
        per-turn output payload, or marked `level=ERROR` on exception
        before re-raising. When `LANGFUSE_*` keys aren't configured
        the trace is a no-op so dev runs aren't slowed down.
        """
        # H-1 / PRD §3.0.5 A: a `redirect` input verdict (self-harm /
        # crisis) force-interrupts the practice. Emit one `moderation`
        # frame carrying the crisis resource, then stop — the roleplay
        # opponent must not reply to a user in distress. No roleplay /
        # coach / judge, no turn persisted, no LLM calls (so no Langfuse
        # trace). validate_turn_request already audited the hit to the
        # ModerationEvent log.
        if validated.input_verdict == "redirect":
            logger.info(
                "turn_user_input_redirect",
                session_id=validated.session_id,
                trace_id=validated.trace_id,
                categories=list(validated.moderation_categories),
            )
            yield SseFrame(
                "moderation",
                {
                    "verdict": "redirect",
                    "categories": list(validated.moderation_categories),
                    "redirect_resource": (
                        validated.redirect_resource.model_dump()
                        if validated.redirect_resource is not None
                        else None
                    ),
                },
            )
            return

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
            # A-40: trace_id + user_id let `record_generation` schedule
            # the per-call DB insert into `llm_calls`. begin_turn_trace
            # hardcodes surface="sandbox" so the rollup endpoint can
            # split sandbox vs review vs copilot spend without each
            # callsite remembering its own surface label.
            trace_id=validated.trace_id,
            user_id=validated.user_id,
            # A-24/A-26/A-29: cross-cutting Langfuse filter tags.
            #   surface:sandbox       — which of the three surfaces this is
            #   minor:{t|f}           — strict-tier moderation was applied
            #   verdict_input:{...}   — moderation decision on user input
            #   verdict_output:{...}  — added mid-trace via add_tags() once
            #                           the roleplay LLM reply is moderated
            #
            # A-29 promoted the single `verdict:` key to
            # `verdict_input:` + `verdict_output:` (matching review).
            # An analyst filtering `verdict_input:block` now gets ONLY
            # turns where the user said something bad; `verdict_output:
            # block` isolates turns where the AI roleplay opponent
            # produced unsafe content. Conflating these previously hid
            # AI-side issues behind the much larger user-side noise.
            tags=[
                "surface:sandbox",
                f"minor:{'true' if validated.is_minor else 'false'}",
                f"verdict_input:{validated.input_verdict}",
            ],
        )

        try:
            seed = get_scenario_seed(session.scenario_id)
            history = _build_history(
                seed_opening=seed.opening_line,
                prior_turns=validated.prior_turns,
            )

            last_opponent_reply = (
                validated.prior_turns[-1].opponent_reply if validated.prior_turns else None
            )

            # PR-L2: resolve the dramatic-arc stage first — it biases the
            # mood arbiter (e.g. a `closing` stage pulls a hostile
            # opponent toward de-escalation). turn_index is 1-based;
            # turns_left counts what remains AFTER this turn so the arc
            # winds down near the cap. arc.update lands before mood.update
            # so the UI stage bar updates ahead of the radar.
            turn_index = len(validated.prior_turns) + 1
            arc = await self._arc_director.resolve(
                turn_index=turn_index,
                turns_left=MAX_TURNS_PER_SESSION - turn_index,
                user_content=validated.content,
                opponent_last_reply=last_opponent_reply,
                trace_id=validated.trace_id,
                session_id=validated.session_id,
            )
            yield SseFrame("arc.update", {"stage": arc.stage})

            # PR-L3: run the mood arbiter so the roleplay prompt reflects
            # the opponent's *updated* mood, not the stale one. The
            # arbiter reads the static persona + previous mood + what the
            # user just said + the arc directive, and falls back to the
            # previous mood on any LLM / parse failure (never blocks the
            # turn). The `mood.update` frame morphs the L9 radar.
            prev_mood = session.mood_vector
            next_mood = await self._mood_arbiter.next_mood(
                character_vector=seed.character_vector,
                prev_mood=prev_mood,
                user_content=validated.content,
                opponent_last_reply=last_opponent_reply,
                trace_id=validated.trace_id,
                session_id=validated.session_id,
                arc_directive=arc.directive,
            )
            if next_mood != prev_mood:
                await self._session_repo.save(replace(session, mood_vector=next_mood))
            yield SseFrame("mood.update", next_mood.to_dict())

            # PR-L6: recall the opponent's memory of this (user, scenario)
            # so the roleplay prompt carries a "你之前交手过 N 次" note.
            # Empty on a first visit; best-effort inside MemoryService.
            episode = await self._memory.recall(
                user_id=validated.user_id,
                scenario_id=session.scenario_id,
            )

            roleplay_messages: list[Message] = [
                Message.system(
                    _build_roleplay_prompt(
                        scenario_title=seed.scenario_title,
                        background=seed.background,
                        persona_title=seed.persona_title,
                        user_goal=session.user_goal,
                        character_descriptor=describe_for_roleplay(next_mood),
                        memory_note=build_memory_note(episode),
                    )
                ),
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

            # A-29: moderate the roleplay LLM's reply. The deltas
            # already streamed live — we can't unsend them — so when
            # mod blocks we replace only the authoritative `done`
            # full_text + the persisted record. Frontend can use the
            # trace's verdict_output tag (or compare delta concat vs
            # done full_text) to decide whether to redact the bubble.
            output_passed, output_verdict = await self._output_passes_moderation(
                full_reply,
                user_id=validated.user_id,
                is_minor=validated.is_minor,
                trace_id=validated.trace_id,
                session_id=validated.session_id,
            )
            if output_verdict is not None:
                trace.add_tags([f"verdict_output:{output_verdict}"])
            if not output_passed:
                logger.warning(
                    "turn_roleplay_output_blocked",
                    session_id=validated.session_id,
                    trace_id=validated.trace_id,
                    verdict=output_verdict,
                )
                full_reply = _ROLEPLAY_REDACTED_PLACEHOLDER

            turn_id = _new_turn_id()
            yield SseFrame(
                "opponent.done",
                {"turn_id": turn_id, "full_text": full_reply},
            )

            coach = await self._coach_three_tones(
                validated.content,
                full_reply,
                trace,
                scenario_title=seed.scenario_title,
                user_goal=session.user_goal,
                opponent_profile=describe_for_coach(next_mood),
            )
            # PR-L8: the strategy read rides the same coach.hint frame as
            # an optional `strategy` object — null when the model went
            # off-vocabulary, so the frontend just omits the card.
            yield SseFrame(
                "coach.hint",
                {
                    "safe": coach.hints.safe,
                    "aggressive": coach.hints.aggressive,
                    "humor": coach.hints.humor,
                    "strategy": coach.strategy.to_dict() if coach.strategy else None,
                },
            )

            # PR-L5: fold this turn's strategy read into the user profile.
            # Best-effort — a stats-store hiccup must never fail the turn.
            if coach.strategy is not None:
                await self._profile.record_safe(
                    user_id=validated.user_id,
                    strategy=coach.strategy.strategy,
                    effect=coach.strategy.effect,
                )

            turn_score = await self._judge_turn(validated.content, full_reply, trace)

            record = TurnRecord(
                turn_id=turn_id,
                session_id=validated.session_id,
                user_content=validated.content,
                opponent_reply=full_reply,
                coach_hint=coach.hints,
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

    async def _output_passes_moderation(
        self,
        roleplay_reply: str,
        *,
        user_id: str,
        is_minor: bool,
        trace_id: str,
        session_id: str,
    ) -> tuple[bool, str | None]:
        """Re-check the roleplay LLM reply against the red-line list.

        Returns `(passes, verdict)`:
          * `passes`: True when the output is safe to surface;
                     False on `block` / `redirect`.
          * `verdict`: the literal moderation verdict for trace
                     tagging. One of:
                       * `"allow"`/`"warn"`/`"redirect"`/`"block"`
                         — backend produced a real decision
                       * `"backend_failed"` (A-34) — backend itself
                         raised; we still treat-as-allow on the
                         UX side but tag the trace so analysts can
                         spot outages without parsing logs
                       * `None` — nothing to check (empty reply
                         after stripping) so we never asked the
                         backend; no `verdict_output:` tag added

        The user already passed input moderation in
        `validate_turn_request`, but the roleplay LLM is a hostile
        adversary by design (it plays the user's opponent) — its
        replies need their own red-line check before we persist
        them or echo them in future turn history.

        Backend errors here are caught and treated as `allow` —
        otherwise an Aliyun outage during sandbox would corrupt the
        per-turn UX. The trace stays untagged so an operator can
        still detect the silent-allow window from backend metrics.
        """
        if not roleplay_reply.strip() or roleplay_reply == _ROLEPLAY_REDACTED_PLACEHOLDER:
            # Nothing meaningful to check — the empty-reply fallback
            # has already redacted any content. Treat-as-allow with
            # no tag so the Langfuse UI is honest about no decision.
            return True, None
        try:
            decision = await self._moderation.check(
                ModerationCheckRequest(
                    content=roleplay_reply,
                    context="ai_output",
                    session_id=session_id,
                ),
                user_id=user_id,
                is_minor=is_minor,
                trace_id=trace_id,
            )
        except ModerationBackendError as exc:
            logger.warning(
                "turn_output_moderation_unavailable",
                session_id=session_id,
                trace_id=trace_id,
                error=str(exc),
            )
            # A-34: return the "backend_failed" sentinel verdict so the
            # caller's existing `verdict_output:{verdict}` tag-add
            # path automatically surfaces outages on the Langfuse UI.
            # `passes=True` keeps the treat-as-allow semantics — UX
            # stays unaffected during a vendor blip. The sentinel
            # rides the same key as real verdicts (allow/warn/...)
            # so analysts only need to know one tag namespace.
            return True, "backend_failed"

        if decision.verdict in ("block", "redirect"):
            return False, decision.verdict
        return True, decision.verdict

    async def _coach_three_tones(
        self,
        user_content: str,
        opponent_reply: str,
        trace: TurnTrace,
        *,
        scenario_title: str,
        user_goal: str,
        opponent_profile: str = "",
    ) -> CoachResult:
        """Single LLM call → parse the three-tone block + the strategy
        read. Fallback on parse fail.

        PR-D4: `scenario_title` + `user_goal` are pinned into the system
        prompt so K speaks from the user's side rather than drifting
        into the opponent's voice.

        PR-L1.3: `opponent_profile` is the compact 3-dim opponent
        descriptor (power_gap / stability / honesty) so K can recommend
        硬刚 vs 缓兵 based on who the user is up against, not just the
        scenario name.

        PR-L8: the same call now also emits a strategy read (what tactic
        the user played, whether it landed, the upgrade). Parsed
        best-effort — `strategy` is None if the model went off-vocabulary,
        and the three-tone hints still come back."""
        prompt = f"用户刚说：{user_content}\n对手回应：{opponent_reply}\n请按三档输出用户的下一句。"
        system_prompt = _build_coach_prompt(
            scenario_title=scenario_title,
            user_goal=user_goal,
            opponent_profile=opponent_profile,
        )
        messages = [Message.system(system_prompt), Message.user(prompt)]
        usage: list[TokenUsage] = []
        raw = await _collect(self._llm.stream_chat(messages, usage_sink=usage))
        trace.record_generation(
            name="coach.three_tones",
            model=self._llm.name,
            input=[m.model_dump() for m in messages],
            output=raw,
            usage=usage[0] if usage else None,
        )
        return CoachResult(hints=_parse_three_tones(raw), strategy=parse_strategy_read(raw))

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
