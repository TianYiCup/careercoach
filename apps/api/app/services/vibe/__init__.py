"""Vibe (daily mood check-in) service package — PRD §7.11.

Public surface:
  * `VibeLogRecord` immutable record + `VibeType` Literal
  * `VibeRepository` Protocol + `InMemoryVibeRepository` +
    `PostgresVibeRepository`
  * `VibeService` — `set_today_vibe`
  * `get_vibe_repository()` + `get_vibe_service()` factory singletons
"""

from functools import lru_cache

import structlog

from app.config import get_settings
from app.db.session import async_session_factory
from app.services.vibe.repository import (
    InMemoryVibeRepository,
    PostgresVibeRepository,
    VibeLogRecord,
    VibeRepository,
    VibeType,
)
from app.services.vibe.service import VibeService

logger = structlog.get_logger(__name__)


@lru_cache(maxsize=1)
def get_vibe_repository() -> VibeRepository:
    """Process-wide vibe repo singleton. Backend chosen via settings —
    `memory` for dev / tests, `postgres` for production. Mirrors the
    `copilot_repo_backend` factory pattern."""
    backend = get_settings().vibe_repo_backend
    if backend == "postgres":
        logger.info("vibe_repository_wired", backend="postgres")
        return PostgresVibeRepository(async_session_factory)
    logger.info("vibe_repository_wired", backend="memory")
    return InMemoryVibeRepository()


@lru_cache(maxsize=1)
def get_vibe_service() -> VibeService:
    """Default wiring for `POST /v1/vibe/today`.

    Singleton so the route layer's `Depends(get_vibe_service)` reuses
    the same repo across requests. Tests override via FastAPI's
    `app.dependency_overrides[get_vibe_service] = ...`.
    """
    repo = get_vibe_repository()
    logger.info("vibe_service_wired", repo=repo.__class__.__name__)
    return VibeService(repo=repo)


__all__ = [
    "InMemoryVibeRepository",
    "PostgresVibeRepository",
    "VibeLogRecord",
    "VibeRepository",
    "VibeService",
    "VibeType",
    "get_vibe_repository",
    "get_vibe_service",
]
