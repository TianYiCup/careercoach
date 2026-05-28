"""Long-term episodic-memory service package — Character Engine L6.

Public surface:
  * `EpisodeRecord` immutable record
  * `EpisodeRepository` Protocol + InMemory + Postgres impls
  * `MemoryService` — `record_safe` / `recall`
  * `build_memory_note` — episode → roleplay-prompt stage direction
  * `get_episode_repository()` + `get_memory_service()` factory singletons
"""

from functools import lru_cache

import structlog

from app.config import get_settings
from app.db.session import async_session_factory
from app.services.memory.repository import (
    EpisodeRecord,
    EpisodeRepository,
    InMemoryEpisodeRepository,
    PostgresEpisodeRepository,
)
from app.services.memory.service import MemoryService, build_memory_note

logger = structlog.get_logger(__name__)


@lru_cache(maxsize=1)
def get_episode_repository() -> EpisodeRepository:
    """Process-wide episode repo singleton. Backend via settings —
    `memory` for dev / tests, `postgres` for production. Mirrors the
    `weakness_repo_backend` / `profile_repo_backend` factory pattern."""
    backend = get_settings().memory_repo_backend
    if backend == "postgres":
        logger.info("episode_repository_wired", backend="postgres")
        return PostgresEpisodeRepository(async_session_factory)
    logger.info("episode_repository_wired", backend="memory")
    return InMemoryEpisodeRepository()


@lru_cache(maxsize=1)
def get_memory_service() -> MemoryService:
    """Default wiring shared by the session-end record (SessionService),
    the session-create recall badge (SessionService), and the per-turn
    roleplay-prompt injection (TurnService). Singleton so all three see
    one repo."""
    repo = get_episode_repository()
    logger.info("memory_service_wired", repo=repo.__class__.__name__)
    return MemoryService(repo=repo)


__all__ = [
    "EpisodeRecord",
    "EpisodeRepository",
    "InMemoryEpisodeRepository",
    "MemoryService",
    "PostgresEpisodeRepository",
    "build_memory_note",
    "get_episode_repository",
    "get_memory_service",
]
