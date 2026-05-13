"""Sandbox session lifecycle service — PRD §7.4.

Public surface:
  * `SessionRecord` + `SessionRepository` Protocol + `InMemorySessionRepository`
  * `TurnRecord` + `CoachHintTrio` + `TurnRepository` + `InMemoryTurnRepository`
  * `SessionService` (create + end) + `TurnService` (turns with SSE)
  * Typed errors mapped at the route layer to 404 / 409 / 400
  * `get_session_service()` + `get_turn_service()` factory singletons
"""

from functools import lru_cache

import structlog

from app.llm.factory import get_llm_router
from app.services.moderation import get_moderation_service
from app.services.sessions.repository import (
    InMemorySessionRepository,
    SessionRecord,
    SessionRepository,
)
from app.services.sessions.service import (
    SessionAlreadyEndedError,
    SessionNotFoundError,
    SessionService,
)
from app.services.sessions.turn_repository import (
    CoachHintTrio,
    InMemoryTurnRepository,
    TurnRecord,
    TurnRepository,
)
from app.services.sessions.turn_service import (
    MAX_TURNS_PER_SESSION,
    SessionEndedForTurnError,
    SessionNotFoundForTurnError,
    TurnService,
    UserInputBlockedError,
)
from app.services.sharecards.session_score import get_session_score_repository

logger = structlog.get_logger(__name__)


@lru_cache(maxsize=1)
def get_session_service() -> SessionService:
    """Default wiring for `POST /v1/sessions` + `POST /v1/sessions/{id}/end`.

    Repository is the same singleton TurnService writes into, so a
    session created here is also visible to `/turns` rebuilding history.
    """
    repository = _get_session_repository()
    score_repo = get_session_score_repository()
    logger.info(
        "session_service_wired",
        repository=repository.__class__.__name__,
        score_repo=score_repo.__class__.__name__,
    )
    return SessionService(repository=repository, score_repo=score_repo)


@lru_cache(maxsize=1)
def get_turn_service() -> TurnService:
    """Default wiring for `POST /v1/sessions/{id}/turns`.

    Shares the session repository singleton with `get_session_service`,
    so a session created via POST /sessions is immediately addressable
    here. The turn repository singleton is also process-wide; PR 4c
    will read it back from `/end` to aggregate the 5-dim Score.
    """
    return TurnService(
        llm=get_llm_router(),
        moderation=get_moderation_service(),
        session_repo=_get_session_repository(),
        turn_repo=_get_turn_repository(),
    )


@lru_cache(maxsize=1)
def _get_session_repository() -> InMemorySessionRepository:
    return InMemorySessionRepository()


@lru_cache(maxsize=1)
def _get_turn_repository() -> InMemoryTurnRepository:
    return InMemoryTurnRepository()


__all__ = [
    "MAX_TURNS_PER_SESSION",
    "CoachHintTrio",
    "InMemorySessionRepository",
    "InMemoryTurnRepository",
    "SessionAlreadyEndedError",
    "SessionEndedForTurnError",
    "SessionNotFoundError",
    "SessionNotFoundForTurnError",
    "SessionRecord",
    "SessionRepository",
    "SessionService",
    "TurnRecord",
    "TurnRepository",
    "TurnService",
    "UserInputBlockedError",
    "get_session_service",
    "get_turn_service",
]
