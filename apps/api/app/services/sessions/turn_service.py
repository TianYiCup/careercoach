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

import asyncio
import uuid
from collections.abc import AsyncIterator
from dataclasses import replace
from datetime import UTC, datetime

import structlog
from langfuse import Langfuse

from app.llm import LLMError, LLMProvider, Message, TokenUsage
from app.observability.langfuse import begin_turn_trace
from app.schemas.moderation import ModerationCheckRequest, RedirectResource
from app.services.memory import MemoryService, build_memory_note, get_memory_service
from app.services.moderation import ModerationBackendError
from app.services.moderation.service import ModerationService
from app.services.profile import ProfileService, get_profile_service
from app.services.scenarios.character_vector import (
    CharacterVector,
    describe_for_coach,
    describe_for_roleplay,
)
from app.services.scenarios.corpus import build_corpus_examples, retrieve
from app.services.sessions.arc_director import ArcDirector
from app.services.sessions.emotional_safety import assess as assess_emotional_harm
from app.services.sessions.emotional_safety import soften as soften_mood
from app.services.sessions.mood_arbiter import MoodArbiter
from app.services.sessions.repository import SessionRepository
from app.services.sessions.scenario_seed import ScenarioSeed, get_scenario_seed
from app.services.sessions.sse import SseFrame
from app.services.sessions.turn_coach_judge import run_coach_three_tones, run_judge
from app.services.sessions.turn_prompts import (
    _ROLEPLAY_FALLBACK_LINE,
    _ROLEPLAY_REDACTED_PLACEHOLDER,
    _build_history,
    _build_roleplay_prompt,
)
from app.services.sessions.turn_repository import (
    TurnRecord,
    TurnRepository,
)
from app.services.sessions.turn_types import (
    SessionEndedForTurnError,
    SessionNotFoundForTurnError,
    UserInputBlockedError,
    ValidatedTurn,
)

logger = structlog.get_logger(__name__)

MAX_TURNS_PER_SESSION = 30
"""PRD §7.4 — soft cap per sandbox session. Surfaced in `meta.turns_left`."""


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
        # arbiter. PR-OPT2: it's now a pure deterministic edge-resolver
        # (no LLM) — middle-window classification rides the arbiter's call.
        self._arc_director = arc_director if arc_director is not None else ArcDirector()
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

        # PR-OPT1: the arc/mood engine runs in this background task. Held
        # at method scope so the `except` can cancel it if the turn errors
        # before it's awaited (otherwise it'd leak as a pending task).
        mood_task: asyncio.Task[tuple[str, CharacterVector]] | None = None
        try:
            seed = get_scenario_seed(session.scenario_id)
            history = _build_history(
                seed_opening=seed.opening_line,
                prior_turns=validated.prior_turns,
            )

            last_opponent_reply = (
                validated.prior_turns[-1].opponent_reply if validated.prior_turns else None
            )
            turn_index = len(validated.prior_turns) + 1
            prev_mood = session.mood_vector

            # PR-L7 (off-ramp redesign): the deep emotional-safety check is
            # heuristic + instant, so it runs *before* roleplay on the mood
            # the opponent is about to speak with (prev_mood). If harm
            # crossed the threshold:
            #   * minor → auto-soften this reply (compliance §3.0.5 C); the
            #     soften lands on the SAME turn (no lag) since roleplay uses
            #     the softened mood below.
            #   * adult → emit a non-blocking `safety.offramp` so K checks
            #     in but the difficulty is NOT lowered — the user keeps
            #     agency and the practice stays real.
            harm = assess_emotional_harm(
                prior_turn_scores=[t.turn_score for t in validated.prior_turns],
                mood=prev_mood,
                is_minor=validated.is_minor,
            )
            roleplay_mood = prev_mood
            if harm.should_intervene and validated.is_minor:
                roleplay_mood = soften_mood(prev_mood)
                logger.info(
                    "emotional_safety_softened",
                    session_id=validated.session_id,
                    trace_id=validated.trace_id,
                    crash_streak=harm.crash_streak,
                    harm=round(harm.harm, 1),
                )
                yield SseFrame("safety.soften", {"crash_streak": harm.crash_streak})
            elif harm.should_intervene:
                logger.info(
                    "emotional_safety_offramp",
                    session_id=validated.session_id,
                    trace_id=validated.trace_id,
                    crash_streak=harm.crash_streak,
                    harm=round(harm.harm, 1),
                )
                yield SseFrame("safety.offramp", {"crash_streak": harm.crash_streak})

            # PR-L3 (concurrency): the opponent replies in the mood it
            # *carried into* this turn (roleplay_mood), so roleplay starts
            # immediately — the user sees the first token in ~1 LLM call,
            # not after arc + arbiter. The arc/mood engine runs in a
            # background task to compute how the mood *shifts* in response
            # to what the user just said; its arc.update / mood.update
            # land after opponent.done (radar morphs right after the reply)
            # and the new mood seeds the next turn.
            mood_task = asyncio.create_task(
                self._resolve_arc_and_mood(
                    seed=seed,
                    prev_mood=prev_mood,
                    user_content=validated.content,
                    opponent_last_reply=last_opponent_reply,
                    turn_index=turn_index,
                    trace_id=validated.trace_id,
                    session_id=validated.session_id,
                )
            )

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
                        character_descriptor=describe_for_roleplay(roleplay_mood),
                        memory_note=build_memory_note(episode),
                        # PR-L4: retrieve real Chinese lines nearest the mood
                        # the opponent is speaking with this turn.
                        corpus_examples=build_corpus_examples(retrieve(roleplay_mood)),
                    )
                ),
                *history,
                Message.user(validated.content),
            ]

            roleplay_usage: list[TokenUsage] = []
            full_reply_parts: list[str] = []
            try:
                async for chunk in self._llm.stream_chat(
                    roleplay_messages, usage_sink=roleplay_usage
                ):
                    if not chunk:
                        continue
                    full_reply_parts.append(chunk)
                    yield SseFrame("opponent.delta", {"text": chunk})
            except LLMError as exc:
                # Both providers missed the first-byte budget (or failed
                # mid-stream). Don't let it kill the SSE stream — that
                # leaves the UI stuck on the typing indicator forever.
                # Fall through to the empty-reply fallback below so the
                # turn still emits opponent.done / coach / meta and
                # terminates cleanly.
                logger.warning(
                    "turn_roleplay_llm_failed",
                    session_id=validated.session_id,
                    trace_id=validated.trace_id,
                    error=str(exc),
                )

            full_reply = "".join(full_reply_parts).strip()
            if not full_reply:
                # LLM hung up before producing anything (or errored) — emit
                # a graceful fallback line so the UI doesn't render an empty
                # bubble or hang on the typing indicator.
                full_reply = _ROLEPLAY_FALLBACK_LINE
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

            # PR-L3: the arc/mood engine ran concurrently with the
            # roleplay stream — collect it now (almost always already
            # done, hidden under the stream). It never raises (arc +
            # arbiter fall back internally). A minor who was softened this
            # turn keeps the de-escalation into the next turn's starting
            # mood, so they aren't re-crushed on the next reply.
            arc_stage, next_mood = await mood_task
            if harm.should_intervene and validated.is_minor:
                next_mood = soften_mood(next_mood)
            if next_mood != prev_mood:
                await self._session_repo.save(replace(session, mood_vector=next_mood))
            yield SseFrame("arc.update", {"stage": arc_stage})
            yield SseFrame("mood.update", next_mood.to_dict())

            # PR-OPT1: coach and judge are independent (both only need the
            # user line + opponent reply) — run them concurrently so the
            # turn finishes in max(coach, judge), not coach + judge.
            coach, turn_score = await asyncio.gather(
                run_coach_three_tones(
                    self._llm,
                    validated.content,
                    full_reply,
                    trace,
                    scenario_title=seed.scenario_title,
                    user_goal=session.user_goal,
                    opponent_profile=describe_for_coach(next_mood),
                ),
                run_judge(self._llm, validated.content, full_reply, trace),
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
            # Don't leak the background arc/mood task if we bailed before
            # awaiting it.
            if mood_task is not None and not mood_task.done():
                mood_task.cancel()
            trace.fail(exc)
            raise

    async def _resolve_arc_and_mood(
        self,
        *,
        seed: ScenarioSeed,
        prev_mood: CharacterVector,
        user_content: str,
        opponent_last_reply: str | None,
        turn_index: int,
        trace_id: str,
        session_id: str,
    ) -> tuple[str, CharacterVector]:
        """Resolve the dramatic-arc stage (L2) and the opponent's next
        mood (L3), returning `(stage, next_mood)`.

        Runs in a background task concurrently with the roleplay stream
        (PR-OPT1) so its LLM call is hidden under the streaming reply
        instead of blocking the first token. PR-OPT2: at the arc's
        deterministic edges the stage is free (no LLM) and only the mood
        needs a call; in the middle window a single merged call
        classifies the stage AND predicts the mood, replacing the
        previous two serial LLM calls. Every sub-step falls back
        internally (arc stage → `conflict`, mood → `prev_mood`), so this
        never raises — the caller can `await` it safely."""
        arc = self._arc_director.resolve(
            turn_index=turn_index,
            turns_left=MAX_TURNS_PER_SESSION - turn_index,
        )
        if arc is not None:
            # Deterministic edge (opening / closing): stage known, so we
            # only spend one LLM call on the mood, biased by the edge
            # directive.
            next_mood = await self._mood_arbiter.next_mood(
                character_vector=seed.character_vector,
                prev_mood=prev_mood,
                user_content=user_content,
                opponent_last_reply=opponent_last_reply,
                trace_id=trace_id,
                session_id=session_id,
                arc_directive=arc.directive,
            )
            return arc.stage, next_mood

        # Middle window: one merged LLM call does both jobs.
        return await self._mood_arbiter.next_mood_with_stage(
            character_vector=seed.character_vector,
            prev_mood=prev_mood,
            user_content=user_content,
            opponent_last_reply=opponent_last_reply,
            trace_id=trace_id,
            session_id=session_id,
        )

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
        if not roleplay_reply.strip() or roleplay_reply in (
            _ROLEPLAY_REDACTED_PLACEHOLDER,
            _ROLEPLAY_FALLBACK_LINE,
        ):
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
