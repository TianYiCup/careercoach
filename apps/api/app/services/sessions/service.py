"""`SessionService` — orchestrates sandbox session lifecycle.

PR 4a flows:

    POST /v1/sessions       → create_session(...)
                            → SessionRepository.save                (audit row)
                            → return CreateSessionResponse

    POST /v1/sessions/{id}/end → end_session(...)
                            → SessionRepository.get / mark_ended
                            → derive a 5-dim Score (stub, until PR 4b)
                            → SessionScoreRepository.add            (powers sharecards)
                            → return EndSessionResponse

PR 4b adds the LangGraph-driven Score in place of the stub, plus the
`/turns` SSE pipe (still 501 here).

Score derivation today
----------------------
`/turns` is still a stub, so there's no turn history to grade. To
unblock the sharecards path (F5 unlocks 卡 live), this service emits a
deterministic placeholder Score on `/end`. Values mirror the MSW mock
(`apps/web/src/mocks/handlers/api.ts`) so the frontend can iterate
against either backend without contract drift. The accompanying log
line `session_ended_with_stub_score` is the canary — once PR 4b lands,
that line should disappear.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import structlog

from app.schemas.sessions import (
    CreateSessionRequest,
    CreateSessionResponse,
    EndSessionResponse,
    Score,
    WeaknessUpdate,
)
from app.services.sessions.repository import (
    SessionRecord,
    SessionRepository,
    utcnow,
)
from app.services.sessions.scenario_seed import ScenarioSeed, get_scenario_seed
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
    ) -> None:
        self._repository = repository
        self._score_repo = score_repo

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

        # Stub score — replaced by Judge-node output in PR 4b. Values
        # mirror apps/web/src/mocks/handlers/api.ts so the contract test
        # stays green when the real backend takes over from MSW.
        score = _stub_score()
        weakness_updates = [WeaknessUpdate(tag="过早让步", delta=1)]

        await self._repository.mark_ended(session_id, ended_at=datetime.now(UTC))

        seed = get_scenario_seed(existing.scenario_id)
        card_data = _score_to_card_data(score, seed=seed)
        # SessionScoreRepository is the read side for `/v1/sharecards/session/{id}`.
        # Writing here is what makes a freshly-ended session render a real card
        # instead of 404.
        self._score_repo.add(session_id, card_data)

        logger.info(
            "session_ended_with_stub_score",
            session_id=session_id,
            result=score.result,
            user_id=user_id,
            replace_in="PR 4b (LangGraph judge integration)",
        )
        return EndSessionResponse(score=score, weakness_updates=weakness_updates)


def _stub_score() -> Score:
    return Score(
        aura=7,
        logic=6,
        emotion=5,
        professionalism=8,
        goal_achieve=4,
        highlights="在压力下保持了专业态度",
        failures="过早让步，没有坚持底线",
        result="guolu",
    )


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
