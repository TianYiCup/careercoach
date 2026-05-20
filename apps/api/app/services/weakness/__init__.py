"""Weakness (communication-weakness profile) service package — PRD §7.7.

Public surface:
  * `WeaknessRecord` immutable record
  * `WeaknessRepository` Protocol + `InMemoryWeaknessRepository` +
    `PostgresWeaknessRepository`
  * `WeaknessService` — `apply_updates` / `apply_safe` / `get_weaknesses`
  * `get_weakness_repository()` + `get_weakness_service()` factory singletons
"""

from functools import lru_cache

import structlog

from app.config import get_settings
from app.db.session import async_session_factory
from app.services.weakness.repository import (
    InMemoryWeaknessRepository,
    PostgresWeaknessRepository,
    WeaknessRecord,
    WeaknessRepository,
)
from app.services.weakness.service import WeaknessService

logger = structlog.get_logger(__name__)


@lru_cache(maxsize=1)
def get_weakness_repository() -> WeaknessRepository:
    """Process-wide weakness repo singleton. Backend chosen via settings —
    `memory` for dev / tests, `postgres` for production. Mirrors the
    `vibe_repo_backend` factory pattern."""
    backend = get_settings().weakness_repo_backend
    if backend == "postgres":
        logger.info("weakness_repository_wired", backend="postgres")
        return PostgresWeaknessRepository(async_session_factory)
    logger.info("weakness_repository_wired", backend="memory")
    return InMemoryWeaknessRepository()


@lru_cache(maxsize=1)
def get_weakness_service() -> WeaknessService:
    """Default wiring for `GET /v1/users/me/weaknesses` + the
    `POST /v1/sessions/{id}/end` weakness fold. Singleton so the read
    side and the session-end write side share one repo."""
    repo = get_weakness_repository()
    logger.info("weakness_service_wired", repo=repo.__class__.__name__)
    return WeaknessService(repo=repo)


__all__ = [
    "InMemoryWeaknessRepository",
    "PostgresWeaknessRepository",
    "WeaknessRecord",
    "WeaknessRepository",
    "WeaknessService",
    "get_weakness_repository",
    "get_weakness_service",
]
