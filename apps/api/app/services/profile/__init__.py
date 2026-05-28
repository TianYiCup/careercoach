"""User strategy-profile service package — Character Engine L5.

Public surface:
  * `StrategyStatRecord` immutable record
  * `ProfileRepository` Protocol + InMemory + Postgres impls
  * `ProfileService` — `record_safe` / `get_stats` / `adapt_vector`
  * `get_profile_repository()` + `get_profile_service()` factory singletons
"""

from functools import lru_cache

import structlog

from app.config import get_settings
from app.db.session import async_session_factory
from app.services.profile.repository import (
    InMemoryProfileRepository,
    PostgresProfileRepository,
    ProfileRepository,
    StrategyStatRecord,
)
from app.services.profile.service import ProfileService

logger = structlog.get_logger(__name__)


@lru_cache(maxsize=1)
def get_profile_repository() -> ProfileRepository:
    """Process-wide profile repo singleton. Backend chosen via settings —
    `memory` for dev / tests, `postgres` for production. Mirrors the
    `weakness_repo_backend` factory pattern."""
    backend = get_settings().profile_repo_backend
    if backend == "postgres":
        logger.info("profile_repository_wired", backend="postgres")
        return PostgresProfileRepository(async_session_factory)
    logger.info("profile_repository_wired", backend="memory")
    return InMemoryProfileRepository()


@lru_cache(maxsize=1)
def get_profile_service() -> ProfileService:
    """Default wiring shared by the per-turn record (TurnService), the
    session-create intensity adaptation (SessionService), and the
    `GET /v1/users/me/profile` read. Singleton so all three see one repo."""
    repo = get_profile_repository()
    logger.info("profile_service_wired", repo=repo.__class__.__name__)
    return ProfileService(repo=repo)


__all__ = [
    "InMemoryProfileRepository",
    "PostgresProfileRepository",
    "ProfileRepository",
    "ProfileService",
    "StrategyStatRecord",
    "get_profile_repository",
    "get_profile_service",
]
