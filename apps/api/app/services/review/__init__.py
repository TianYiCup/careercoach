"""Review-mode (复盘师) service package — PRD §7.6.

Public surface:
  * `ReviewUploadRecord` + `ReviewTurnRecord` immutable records (A-9)
  * `ReviewRepository` Protocol + InMemory + Postgres impls (A-9)
  * `ReviewStatus` / `ReviewVerdict` / `Speaker` Literal types (A-9)
  * `ReviewService` orchestrator + `get_review_service()` factory (A-11)
  * `get_review_repository()` factory singleton (A-9)
"""

from functools import lru_cache

import structlog

from app.config import get_settings
from app.db.session import async_session_factory
from app.llm.factory import get_llm_router
from app.services.moderation import get_moderation_service
from app.services.review.repository import (
    InMemoryReviewRepository,
    PostgresReviewRepository,
    ReviewRepository,
    ReviewStatus,
    ReviewTurnRecord,
    ReviewUploadRecord,
    ReviewVerdict,
    Speaker,
)
from app.services.review.service import ReviewInputBlockedError, ReviewService

logger = structlog.get_logger(__name__)


@lru_cache(maxsize=1)
def get_review_repository() -> ReviewRepository:
    """Process-wide review repo singleton. Backend chosen via settings —
    `memory` for dev / tests, `postgres` for production. Mirrors the
    `sessions_repo_backend` factory pattern so the operational story
    (env var name + memory default) is identical across services."""
    backend = get_settings().review_repo_backend
    if backend == "postgres":
        logger.info("review_repository_wired", backend="postgres")
        return PostgresReviewRepository(async_session_factory)
    logger.info("review_repository_wired", backend="memory")
    return InMemoryReviewRepository()


@lru_cache(maxsize=1)
def get_review_service() -> ReviewService:
    """Default wiring for `POST /v1/review/uploads` + the GET detail route.

    Singleton so the route layer's `Depends(get_review_service)` reuses
    the same repo + LLM router + moderation singletons across requests
    — matches the `get_session_service` precedent. Tests override via
    FastAPI's `app.dependency_overrides[get_review_service] = ...`.
    """
    repo = get_review_repository()
    provider = get_llm_router()
    moderation = get_moderation_service()
    logger.info(
        "review_service_wired",
        repo=repo.__class__.__name__,
        llm=provider.__class__.__name__,
        moderation=moderation.__class__.__name__,
    )
    return ReviewService(repo=repo, provider=provider, moderation=moderation)


__all__ = [
    "InMemoryReviewRepository",
    "PostgresReviewRepository",
    "ReviewInputBlockedError",
    "ReviewRepository",
    "ReviewService",
    "ReviewStatus",
    "ReviewTurnRecord",
    "ReviewUploadRecord",
    "ReviewVerdict",
    "Speaker",
    "get_review_repository",
    "get_review_service",
]
