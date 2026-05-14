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
    CreateSessionRequest,
    CreateSessionResponse,
    EndSessionResponse,
    Score,
    WeaknessUpdate,
)
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
    ) -> None:
        self._repository = repository
        self._score_repo = score_repo
        self._turn_repo = turn_repo
        self._llm = llm

    async def create_session(
        self,
        request: CreateSessionRequest,
        *,
        user_id: str,
    ) -> CreateSessionResponse:
        session_id = _new_session_id()
        seed = get_scenario_seed(request.scenario_id)

        record = SessionRecord(
            session_id=session_id,
            user_id=user_id,
            mode=request.mode,
            scenario_id=request.scenario_id,
            persona_id=request.persona_id,
            user_goal=request.user_goal,
            status="active",
            created_at=utcnow(),
        )
        await self._repository.save(record)

        logger.info(
            "session_created",
            session_id=session_id,
            scenario_id=request.scenario_id,
            mode=request.mode,
        )
        return CreateSessionResponse(
            session_id=session_id,
            opening_line=seed.opening_line,
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

        logger.info(
            "session_ended",
            session_id=session_id,
            user_id=user_id,
            result=score.result,
            turns=len(turns),
        )
        return EndSessionResponse(
            score=score,
            weakness_updates=_derive_weakness_updates(turns_count=len(turns)),
        )


def _derive_weakness_updates(*, turns_count: int) -> list[WeaknessUpdate]:
    """Per-tag delta list returned alongside `Score`.

    The full taxonomy-driven weakness tracker is a later epic; for now
    we just emit a single placeholder when there are turns to score, and
    nothing for an empty session so the response isn't lying.
    """
    if turns_count == 0:
        return []
    return [WeaknessUpdate(tag="过早让步", delta=1)]


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
