"""Sandbox session lifecycle service — PRD §7.4.

Public surface:
  * `SessionRecord` + `SessionRepository` Protocol + `InMemorySessionRepository`
  * `SessionService` orchestrator
  * `SessionNotFoundError` / `SessionAlreadyEndedError` for the route layer
  * `get_session_service()` factory (lru-cached singleton)
"""

from functools import lru_cache

import structlog

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
from app.services.sharecards.session_score import get_session_score_repository

logger = structlog.get_logger(__name__)


@lru_cache(maxsize=1)
def get_session_service() -> SessionService:
    """Default wiring for the route layer.

    Wiring rules:
      * Repository is `InMemorySessionRepository` until PR 4b adds a
        DB-backed one. Process-wide singleton so /sessions create and
        /end see the same store.
      * Score repository is `get_session_score_repository()` — the
        same singleton the share-card service reads. That shared seam
        is what makes a freshly-ended session render a real card.
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
def _get_session_repository() -> InMemorySessionRepository:
    return InMemorySessionRepository()


__all__ = [
    "InMemorySessionRepository",
    "SessionAlreadyEndedError",
    "SessionNotFoundError",
    "SessionRecord",
    "SessionRepository",
    "SessionService",
    "get_session_service",
]
