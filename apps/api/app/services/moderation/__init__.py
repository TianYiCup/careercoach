"""Content moderation service (PRD §7.8 / §3.0.5 red-line).

Public surface:
  * `ModerationService` — orchestrator; injected into the route layer.
  * `ModerationBackend` Protocol + concrete backends.
  * `ModerationEventSink` Protocol + DB / log sinks.
  * `get_moderation_service()` — FastAPI dependency that builds the
    default wiring (cloud → local cascade when AK is set, local-only
    otherwise).
"""

from __future__ import annotations

from functools import lru_cache

import structlog

from app.config import get_settings
from app.db.session import async_session_factory
from app.services.moderation.aliyun import AliyunTextModerationBackend
from app.services.moderation.backend import ModerationBackend, ModerationBackendError
from app.services.moderation.cascading import CascadingBackend
from app.services.moderation.event_sink import (
    DbEventSink,
    LogOnlyEventSink,
    ModerationEventSink,
)
from app.services.moderation.local_dict import DictBackend
from app.services.moderation.noop import NoopBackend
from app.services.moderation.service import ModerationService
from app.services.moderation.types import Decision

logger = structlog.get_logger(__name__)


@lru_cache(maxsize=1)
def get_moderation_service() -> ModerationService:
    """Default service wiring used by the route layer.

    Wiring rules:
      * AK + secret both set → `CascadingBackend(aliyun, local_dict)`.
        Cloud goes first with an 800 ms budget; on timeout / upstream
        failure we fall through to the bundled local dict.
      * either empty → `DictBackend` alone. This keeps dev / CI / tests
        running without Aliyun credentials and degrades gracefully in
        air-gapped deploys.
    """
    settings = get_settings()
    local: ModerationBackend = DictBackend.from_file()

    ak_id = settings.aliyun_access_key_id.get_secret_value()
    ak_secret = settings.aliyun_access_key_secret.get_secret_value()

    backend: ModerationBackend
    if ak_id and ak_secret:
        cloud = AliyunTextModerationBackend.from_settings(settings)
        backend = CascadingBackend(
            primary=cloud,
            backup=local,
            timeout_s=settings.aliyun_moderation_timeout_s,
        )
        logger.info("moderation_backend_wired", mode="cascading", primary="aliyun")
    else:
        backend = local
        logger.info("moderation_backend_wired", mode="local_only", reason="aliyun_keys_empty")

    sink: ModerationEventSink = DbEventSink(async_session_factory)
    return ModerationService(backend=backend, event_sink=sink)


__all__ = [
    "AliyunTextModerationBackend",
    "CascadingBackend",
    "DbEventSink",
    "Decision",
    "DictBackend",
    "LogOnlyEventSink",
    "ModerationBackend",
    "ModerationBackendError",
    "ModerationEventSink",
    "ModerationService",
    "NoopBackend",
    "get_moderation_service",
]
