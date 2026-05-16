"""Copilot (副驾 / live-coaching) service package — PRD §7.5.

Public surface (A-15 — persistence + create_session):
  * `CopilotSessionRecord` immutable record
  * `CopilotRepository` Protocol + `InMemoryCopilotRepository` +
    `PostgresCopilotRepository`
  * `CopilotStatus` / `PrivacyLevel` Literal types
  * `CopilotService.create_session` orchestrator
  * `get_copilot_repository()` + `get_copilot_service()` factory
    singletons

A-16 will add the ASR adapter abstraction next to this package.
A-17 adds the WebSocket endpoint + status-transition methods on the
repo (`mark_connected` / `mark_ended` already shipped here so the
WS layer just calls them).
"""

from functools import lru_cache

import structlog

from app.config import get_settings
from app.db.session import async_session_factory
from app.services.copilot.repository import (
    CopilotRepository,
    CopilotSessionRecord,
    CopilotStatus,
    InMemoryCopilotRepository,
    PostgresCopilotRepository,
    PrivacyLevel,
)
from app.services.copilot.service import CopilotService

logger = structlog.get_logger(__name__)


@lru_cache(maxsize=1)
def get_copilot_repository() -> CopilotRepository:
    """Process-wide copilot repo singleton. Backend chosen via settings —
    `memory` for dev / tests, `postgres` for production. Mirrors the
    `sessions_repo_backend` factory pattern."""
    backend = get_settings().copilot_repo_backend
    if backend == "postgres":
        logger.info("copilot_repository_wired", backend="postgres")
        return PostgresCopilotRepository(async_session_factory)
    logger.info("copilot_repository_wired", backend="memory")
    return InMemoryCopilotRepository()


@lru_cache(maxsize=1)
def get_copilot_service() -> CopilotService:
    """Default wiring for `POST /v1/copilot/sessions`.

    Singleton so route layer's `Depends(get_copilot_service)` reuses
    the same repo across requests. Tests override via FastAPI's
    `app.dependency_overrides[get_copilot_service] = ...`.
    """
    repo = get_copilot_repository()
    ws_base_url = get_settings().copilot_ws_base_url
    logger.info(
        "copilot_service_wired",
        repo=repo.__class__.__name__,
        ws_base_url=ws_base_url,
    )
    return CopilotService(repo=repo, ws_base_url=ws_base_url)


__all__ = [
    "CopilotRepository",
    "CopilotService",
    "CopilotSessionRecord",
    "CopilotStatus",
    "InMemoryCopilotRepository",
    "PostgresCopilotRepository",
    "PrivacyLevel",
    "get_copilot_repository",
    "get_copilot_service",
]
