"""Read-side service package for moderation-event audit rows (A-43).

Public surface:
  * `ModerationEventRecord` immutable record
  * `ModerationEventRepository` Protocol + `InMemoryModerationEventRepository`
    + `PostgresModerationEventRepository`
  * `get_moderation_event_repository()` factory singleton

The write path stays on `app.services.moderation.event_sink.DbEventSink`
— this package is read-only. A-43 ships the rollup endpoint
`GET /v1/ops/moderation-events` against `list_events(...)`.
"""

from functools import lru_cache

import structlog

from app.config import get_settings
from app.db.session import async_session_factory
from app.services.moderation_events.repository import (
    InMemoryModerationEventRepository,
    ModerationEventAggregate,
    ModerationEventRecord,
    ModerationEventRepository,
    ModerationStatsBreakdownEntry,
    ModerationStatsTotals,
    PostgresModerationEventRepository,
)

logger = structlog.get_logger(__name__)


@lru_cache(maxsize=1)
def get_moderation_event_repository() -> ModerationEventRepository:
    """Process-wide moderation-event repo singleton. Backend chosen
    via settings — `memory` for dev/tests, `postgres` for production.
    Mirrors the `llm_calls_repo_backend` factory pattern."""
    backend = get_settings().moderation_events_repo_backend
    if backend == "postgres":
        logger.info("moderation_event_repository_wired", backend="postgres")
        return PostgresModerationEventRepository(async_session_factory)
    logger.info("moderation_event_repository_wired", backend="memory")
    return InMemoryModerationEventRepository()


__all__ = [
    "InMemoryModerationEventRepository",
    "ModerationEventAggregate",
    "ModerationEventRecord",
    "ModerationEventRepository",
    "ModerationStatsBreakdownEntry",
    "ModerationStatsTotals",
    "PostgresModerationEventRepository",
    "get_moderation_event_repository",
]
