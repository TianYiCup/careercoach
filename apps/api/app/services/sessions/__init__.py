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

from app.config import get_settings
from app.db.session import async_session_factory
from app.llm.factory import get_llm_router
from app.services.moderation import get_moderation_service
from app.services.sessions.repository import (
    InMemorySessionRepository,
    PostgresSessionRepository,
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
    PostgresTurnRepository,
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

    The session repository, turn repository, and LLM router are all
    shared singletons with `get_turn_service` so a single in-process
    instance sees consistent state across create → turns → end.
    """
    repository = _get_session_repository()
    score_repo = get_session_score_repository()
    turn_repo = _get_turn_repository()
    llm = get_llm_router()
    logger.info(
        "session_service_wired",
        repository=repository.__class__.__name__,
        score_repo=score_repo.__class__.__name__,
        turn_repo=turn_repo.__class__.__name__,
        llm=llm.__class__.__name__,
    )
    return SessionService(
        repository=repository,
        score_repo=score_repo,
        turn_repo=turn_repo,
        llm=llm,
    )


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
def _get_session_repository() -> SessionRepository:
    """Process-wide session repo singleton. Backend chosen via settings —
    `memory` for dev / tests, `postgres` for production. The factory's
    `lru_cache` means SessionService and TurnService share the same
    instance, so a session created here is visible to `/turns` and `/end`."""
    backend = get_settings().sessions_repo_backend
    if backend == "postgres":
        logger.info("session_repository_wired", backend="postgres")
        return PostgresSessionRepository(async_session_factory)
    logger.info("session_repository_wired", backend="memory")
    return InMemorySessionRepository()


@lru_cache(maxsize=1)
def _get_turn_repository() -> TurnRepository:
    """Process-wide turn repo singleton; same backend selection rule."""
    backend = get_settings().sessions_repo_backend
    if backend == "postgres":
        logger.info("turn_repository_wired", backend="postgres")
        return PostgresTurnRepository(async_session_factory)
    logger.info("turn_repository_wired", backend="memory")
    return InMemoryTurnRepository()


__all__ = [
    "MAX_TURNS_PER_SESSION",
    "CoachHintTrio",
    "InMemorySessionRepository",
    "InMemoryTurnRepository",
    "PostgresSessionRepository",
    "PostgresTurnRepository",
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
