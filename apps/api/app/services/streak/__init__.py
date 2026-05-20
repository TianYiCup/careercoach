"""Streak (consecutive practice-days) service package — PRD §7.11.

Public surface:
  * `StreakRecord` immutable record
  * `StreakRepository` Protocol + `InMemoryStreakRepository` +
    `PostgresStreakRepository`
  * `StreakService` — `touch` / `touch_safe` / `get_streak`
  * `get_streak_repository()` + `get_streak_service()` factory singletons
"""

from functools import lru_cache

import structlog

from app.config import get_settings
from app.db.session import async_session_factory
from app.services.streak.repository import (
    InMemoryStreakRepository,
    PostgresStreakRepository,
    StreakRecord,
    StreakRepository,
)
from app.services.streak.service import StreakService

logger = structlog.get_logger(__name__)


@lru_cache(maxsize=1)
def get_streak_repository() -> StreakRepository:
    """Process-wide streak repo singleton. Backend chosen via settings —
    `memory` for dev / tests, `postgres` for production. Mirrors the
    `vibe_repo_backend` factory pattern."""
    backend = get_settings().streak_repo_backend
    if backend == "postgres":
        logger.info("streak_repository_wired", backend="postgres")
        return PostgresStreakRepository(async_session_factory)
    logger.info("streak_repository_wired", backend="memory")
    return InMemoryStreakRepository()


@lru_cache(maxsize=1)
def get_streak_service() -> StreakService:
    """Default wiring for `GET /v1/streak` + the `POST /v1/sessions`
    streak touch. Singleton so the read side and the touch side share
    one repo. Tests override via `app.dependency_overrides`."""
    repo = get_streak_repository()
    logger.info("streak_service_wired", repo=repo.__class__.__name__)
    return StreakService(repo=repo)


__all__ = [
    "InMemoryStreakRepository",
    "PostgresStreakRepository",
    "StreakRecord",
    "StreakRepository",
    "StreakService",
    "get_streak_repository",
    "get_streak_service",
]
