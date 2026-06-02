"""Review-mode (复盘师) service package — PRD §7.6.

Public surface:
  * `ReviewUploadRecord` + `ReviewTurnRecord` immutable records (A-9)
  * `ReviewRepository` Protocol + InMemory + Postgres impls (A-9)
  * `ReviewStatus` / `ReviewVerdict` / `Speaker` Literal types (A-9)
  * `ReviewService` orchestrator + `get_review_service()` factory (A-11)
  * `ReviewInputBlockedError` (A-12)
  * `ReviewWorkerQueue` + `InProcessWorkerQueue` (A-13)
  * `get_review_repository()` factory singleton (A-9)
"""

from functools import lru_cache

import structlog

from app.config import get_settings
from app.db.session import async_session_factory
from app.llm.factory import get_llm_router
from app.observability.langfuse import get_langfuse_client
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
from app.services.review.worker import (
    InProcessWorkerQueue,
    ReviewWorkerQueue,
    SyncWorkerQueue,
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


@lru_cache(maxsize=1)
def get_review_worker_queue() -> ReviewWorkerQueue:
    """Process-wide background queue singleton for review analysis.

    v0 ships `SyncWorkerQueue` — it runs the analysis inline so the
    POST returns a terminal (`done` / `failed`) record. The shipped
    web/EXE result page fetches the upload once and does NOT poll, so an
    async `processing` response would strand it on an empty screen (the
    reported "复盘功能异常"). Inline analysis fits v0's single-uvicorn
    deploy (foundation §3.1); the ~1-2s LLM round-trip sits behind the
    upload page's existing "分析中…" spinner.

    `InProcessWorkerQueue` (asyncio.create_task) is the async
    alternative for when the client grows a polling loop + the A-14
    Redis / Celery / Cloud-Tasks migration; it's a one-line swap here
    since the Protocol is shared. Tests still override with their own
    `_SyncWorkerQueue` / `_DeferredQueue` / `InProcessWorkerQueue` for
    deterministic assertions.
    """
    queue: ReviewWorkerQueue = SyncWorkerQueue()
    logger.info("review_worker_queue_wired", backend=queue.name)
    return queue


@lru_cache(maxsize=1)
def get_review_service() -> ReviewService:
    """Default wiring for `POST /v1/review/uploads` + the GET detail route.

    Singleton so the route layer's `Depends(get_review_service)` reuses
    the same repo + LLM router + moderation + queue instances across
    requests — matches the `get_session_service` precedent. Tests
    override via FastAPI's `app.dependency_overrides[get_review_service]
    = ...`.
    """
    repo = get_review_repository()
    provider = get_llm_router()
    moderation = get_moderation_service()
    queue = get_review_worker_queue()
    # `None` when LANGFUSE_* keys are unset — `begin_review_trace`
    # short-circuits to a no-op `TurnTrace`. Dev without a local
    # Langfuse instance works unchanged.
    langfuse_client = get_langfuse_client()
    logger.info(
        "review_service_wired",
        repo=repo.__class__.__name__,
        llm=provider.__class__.__name__,
        moderation=moderation.__class__.__name__,
        queue=queue.name,
        langfuse_enabled=langfuse_client is not None,
    )
    return ReviewService(
        repo=repo,
        provider=provider,
        moderation=moderation,
        queue=queue,
        langfuse_client=langfuse_client,
    )


__all__ = [
    "InMemoryReviewRepository",
    "InProcessWorkerQueue",
    "PostgresReviewRepository",
    "ReviewInputBlockedError",
    "ReviewRepository",
    "ReviewService",
    "ReviewStatus",
    "ReviewTurnRecord",
    "ReviewUploadRecord",
    "ReviewVerdict",
    "ReviewWorkerQueue",
    "Speaker",
    "SyncWorkerQueue",
    "get_review_repository",
    "get_review_service",
    "get_review_worker_queue",
]
