"""Content moderation service (PRD §7.8 / §3.0.5 red-line).

Public surface:
  * `ModerationService` — orchestrator; injected into the route layer.
  * `ModerationBackend` Protocol + concrete backends.
  * `ModerationEventSink` Protocol + DB / log sinks.
  * `get_moderation_service()` — FastAPI dependency that builds the
    default wiring (Noop backend + DB sink with log fallback).
"""

from __future__ import annotations

from functools import lru_cache

import structlog

from app.db.session import async_session_factory
from app.services.moderation.backend import ModerationBackend, ModerationBackendError
from app.services.moderation.event_sink import (
    DbEventSink,
    LogOnlyEventSink,
    ModerationEventSink,
)
from app.services.moderation.noop import NoopBackend
from app.services.moderation.service import ModerationService
from app.services.moderation.types import Decision

logger = structlog.get_logger(__name__)


@lru_cache(maxsize=1)
def get_moderation_service() -> ModerationService:
    """Default service wiring used by the route layer.

    `NoopBackend` is intentional in PR ① — PR ② swaps it for the local
    keyword dict, PR ③ adds the cloud cascade. The wiring stays here.
    """
    backend: ModerationBackend = NoopBackend()
    sink: ModerationEventSink = DbEventSink(async_session_factory)
    return ModerationService(backend=backend, event_sink=sink)


__all__ = [
    "DbEventSink",
    "Decision",
    "LogOnlyEventSink",
    "ModerationBackend",
    "ModerationBackendError",
    "ModerationEventSink",
    "ModerationService",
    "NoopBackend",
    "get_moderation_service",
]
