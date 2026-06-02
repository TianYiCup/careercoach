"""`SessionService` — orchestrates sandbox session lifecycle.

Endpoints owned here:

    POST /v1/sessions       → create_session(...)
                            → SessionRepository.save                (audit row)
                            → return CreateSessionResponse

    POST /v1/sessions/{id}/end → end_session(...)
                            → SessionRepository.get / mark_ended
                            → aggregator.aggregate_session_score
                              (reads TurnRepository for history, calls
                               LLM once for the 5-dim summary, falls back
                               to mechanical aggregation on failure)
                            → SessionScoreRepository.add            (powers sharecards)
                            → return EndSessionResponse

The `/turns` SSE pipeline lives in `TurnService`; it writes per-turn
records into the same `TurnRepository` this service reads on `/end`.

PR history:
  * PR 4a — skeleton with hardcoded stub score
  * PR 4b — `TurnService` + SSE turns pipeline
  * PR 4c — wire the aggregator, drop the stub
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import structlog

from app.llm import LLMProvider
from app.schemas.sessions import (
    CharacterVectorPayload,
    CreateSessionRequest,
    CreateSessionResponse,
    EndSessionResponse,
    Score,
    ScoreResult,
    SessionMemoryPayload,
    WeaknessUpdate,
)
from app.services.memory import MemoryService, get_memory_service
from app.services.profile import ProfileService, get_profile_service
from app.services.sessions.aggregator import aggregate_session_score
from app.services.sessions.repository import (
    SessionRecord,
    SessionRepository,
    utcnow,
)
from app.services.sessions.scenario_seed import ScenarioSeed, get_scenario_seed
from app.services.sessions.turn_repository import TurnRepository
from app.services.sharecards.session_score import SessionScoreRepository
from app.services.sharecards.types import SessionCardData

logger = structlog.get_logger(__name__)


class SessionNotFoundError(LookupError):
    """Route maps to 404."""


class SessionAlreadyEndedError(RuntimeError):
    """Route maps to 409 — `/end` called twice on the same session."""


class SessionService:
    """Owns the session row + the derived score handoff to sharecards."""

    def __init__(
        self,
        *,
        repository: SessionRepository,
        score_repo: SessionScoreRepository,
        turn_repo: TurnRepository,
        llm: LLMProvider,
        profile_service: ProfileService | None = None,
        memory_service: MemoryService | None = None,
    ) -> None:
        self._repository = repository
        self._score_repo = score_repo
        self._turn_repo = turn_repo
        self._llm = llm
        # PR-L5: adapts the opponent's starting vector to the user's
        # profile (softer for beginners, harder on their crutch strategy).
        # Shared singleton so it sees the same stats TurnService records.
        self._profile = profile_service if profile_service is not None else get_profile_service()
        # PR-L6: records the episode at session end + recalls it at create
        # for the "对手记得你" badge. Shared singleton so TurnService's
        # per-turn recall sees what session end wrote.
        self._memory = memory_service if memory_service is not None else get_memory_service()

    async def create_session(
        self,
        request: CreateSessionRequest,
        *,
        user_id: str,
    ) -> CreateSessionResponse:
        session_id = _new_session_id()
        seed = get_scenario_seed(request.scenario_id)

        # PR-L5: scale the scenario's static profile to this user before
        # it becomes the session's starting mood + the radar's first
        # shape. Best-effort inside the service — returns base on error.
        adapted_vector = await self._profile.adapt_vector(
            user_id=user_id,
            base=seed.character_vector,
        )

        record = SessionRecord(
            session_id=session_id,
            user_id=user_id,
            mode=request.mode,
            scenario_id=request.scenario_id,
            persona_id=request.persona_id,
            user_goal=request.user_goal,
            status="active",
            created_at=utcnow(),
            # PR-L3: seed live mood = the L5-adapted profile. The
            # MoodArbiter mutates this on every user turn going forward.
            mood_vector=adapted_vector,
        )
        await self._repository.save(record)

        logger.info(
            "session_created",
            session_id=session_id,
            scenario_id=request.scenario_id,
            mode=request.mode,
        )

        # L6: surface the opponent's memory of this (user, scenario) so
        # the frontend can show a "对手记得你" badge from the first frame.
        episode = await self._memory.recall(
            user_id=user_id,
            scenario_id=request.scenario_id,
        )
        memory_payload = (
            SessionMemoryPayload(
                visit_count=episode.visit_count,
                last_result=episode.last_result,
            )
            if episode is not None and episode.visit_count >= 1
            else None
        )

        return CreateSessionResponse(
            session_id=session_id,
            opening_line=seed.opening_line,
            # L5: the radar shows the *adapted* vector — what this user
            # actually faces — not the raw catalog profile.
            character_vector=CharacterVectorPayload(**adapted_vector.to_dict()),
            memory=memory_payload,
        )

    async def end_session(
        self,
        session_id: str,
        *,
        user_id: str,
    ) -> EndSessionResponse:
        existing = await self._repository.get(session_id)
        if existing is None:
            raise SessionNotFoundError(session_id)
        if existing.status == "ended":
            raise SessionAlreadyEndedError(session_id)

        seed = get_scenario_seed(existing.scenario_id)
        turns = await self._turn_repo.list_for_session(session_id)
        score = await aggregate_session_score(
            turns=turns,
            llm=self._llm,
            user_goal=existing.user_goal,
            scenario_title=seed.scenario_title,
        )

        await self._repository.mark_ended(session_id, ended_at=datetime.now(UTC))

        card_data = _score_to_card_data(score, seed=seed)
        # SessionScoreRepository is the read side for `/v1/sharecards/session/{id}`.
        # Writing here is what makes a freshly-ended session render a real card
        # instead of 404.
        await self._score_repo.add(session_id, card_data)

        # L6: remember this session so the opponent recalls it next time
        # the user practises this scenario. Only record sessions with
        # actual turns — an empty session has no story worth remembering.
        # Best-effort: a memory-store hiccup must not fail the scorecard.
        if turns:
            await self._memory.record_safe(
                user_id=user_id,
                scenario_id=existing.scenario_id,
                result=score.result,
                takeaway=score.failures,
            )

        logger.info(
            "session_ended",
            session_id=session_id,
            user_id=user_id,
            result=score.result,
            turns=len(turns),
        )
        return EndSessionResponse(
            score=score,
            weakness_updates=_derive_weakness_updates(score=score, turns_count=len(turns)),
        )


# Coarse, outcome-driven weakness tags for the sandbox path. A 封神 win
# is deliberately absent — winning a round is not a weak spot, and the
# old code's "every session adds 过早让步" made the weakness panel lie.
# The specific, actionable weak points come from the 复盘 path (the
# reviewer's top-failures); this is the coarse "how did the round go"
# companion signal.
_RESULT_WEAKNESS_TAG: dict[ScoreResult, str] = {
    "fanche": "对抗中落于下风",
    "guolu": "未能掌控对话节奏",
}


def _derive_weakness_updates(*, score: Score, turns_count: int) -> list[WeaknessUpdate]:
    """Per-tag delta list returned alongside `Score`.

    Gated on the real outcome: an empty session and a 封神 win both
    contribute nothing (so the response doesn't invent a weakness the
    user didn't earn); a 路过 / 翻车 folds in one coarse outcome tag.
    """
    if turns_count == 0:
        return []
    tag = _RESULT_WEAKNESS_TAG.get(score.result)
    if tag is None:
        return []
    return [WeaknessUpdate(tag=tag, delta=1)]


def _score_to_card_data(score: Score, *, seed: ScenarioSeed) -> SessionCardData:
    """Adapt the HTTP `Score` into the renderer's `SessionCardData`.

    Lives here rather than in either schema package because it's a
    seam between sessions (writer) and sharecards (reader) — neither
    of them should own the conversion alone.
    """
    return SessionCardData(
        scenario_title=seed.scenario_title,
        persona_title=seed.persona_title,
        aura=score.aura,
        logic=score.logic,
        emotion=score.emotion,
        professionalism=score.professionalism,
        goal_achieve=score.goal_achieve,
        result=score.result,
        highlights=score.highlights,
    )


def _new_session_id() -> str:
    """`ses_<16-hex>` matching schemas/sessions.py example."""
    return f"ses_{uuid.uuid4().hex[:16]}"
