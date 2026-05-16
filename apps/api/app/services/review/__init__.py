"""Review-mode (复盘师) service package — PRD §7.6.

Public surface (v0 / A-9):
  * `ReviewUploadRecord` + `ReviewTurnRecord` immutable records
  * `ReviewRepository` Protocol + InMemory + Postgres impls
  * `ReviewStatus` / `ReviewVerdict` / `Speaker` Literal types
  * `get_review_repository()` factory singleton

The reviewer LangGraph chain (A-10) and route layer (A-11) will
extend this surface without breaking the persistence contract.
"""

from functools import lru_cache

import structlog

from app.config import get_settings
from app.db.session import async_session_factory
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


__all__ = [
    "InMemoryReviewRepository",
    "PostgresReviewRepository",
    "ReviewRepository",
    "ReviewStatus",
    "ReviewTurnRecord",
    "ReviewUploadRecord",
    "ReviewVerdict",
    "Speaker",
    "get_review_repository",
]
