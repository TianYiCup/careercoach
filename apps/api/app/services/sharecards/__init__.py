"""Wrapped 卡分享服务 — PR ③ wires the renderer to storage + DB.

Public surface:
  * `ShareCardRenderer` Protocol + `PillowShareCardRenderer` (PR ②)
  * `ShareCardStorage` Protocol + `LocalFilesystemStorage` (PR ③)
  * `SessionScoreRepository` Protocol + `InMemorySessionScoreRepository` (PR ③)
  * `ShareCardService` orchestrator + `get_sharecard_service()` factory
"""

from functools import lru_cache
from pathlib import Path

import structlog

from app.config import get_settings
from app.db.session import async_session_factory
from app.services.moderation import get_moderation_service
from app.services.sharecards.pillow_renderer import PillowShareCardRenderer
from app.services.sharecards.renderer import (
    CARD_HEIGHT,
    CARD_WIDTH,
    ShareCardRenderer,
    ShareCardRendererError,
)
from app.services.sharecards.service import (
    ShareCardCaptionBlockedError,
    ShareCardNotFoundError,
    ShareCardService,
)
from app.services.sharecards.session_score import (
    InMemorySessionScoreRepository,
    SessionScoreRepository,
)
from app.services.sharecards.storage import LocalFilesystemStorage, ShareCardStorage
from app.services.sharecards.types import SessionCardData

logger = structlog.get_logger(__name__)


@lru_cache(maxsize=1)
def get_sharecard_service() -> ShareCardService:
    """Default service wiring used by the route layer.

    Wiring rules:
      * Renderer is always `PillowShareCardRenderer` for v0.
      * Storage is `LocalFilesystemStorage` writing to `sharecards_storage_dir`
        and serving under `sharecards_public_base_url`.
      * Score repository is the empty `InMemorySessionScoreRepository`
        until Sprint-2 drops a DB-backed implementation; until then,
        the route returns 404 for every session_id.
      * Moderation is the same singleton used by `/v1/moderation/check`.
    """
    settings = get_settings()
    storage_root = Path(settings.sharecards_storage_dir)

    renderer = PillowShareCardRenderer(app_share_origin=settings.sharecards_app_origin)
    storage = LocalFilesystemStorage(
        root=storage_root,
        public_base_url=settings.sharecards_public_base_url,
    )
    score_repo = InMemorySessionScoreRepository()
    moderation = get_moderation_service()

    logger.info(
        "sharecard_service_wired",
        renderer=renderer.name,
        storage=storage.name,
        storage_root=str(storage_root),
        public_base_url=settings.sharecards_public_base_url,
    )

    return ShareCardService(
        renderer=renderer,
        storage=storage,
        score_repo=score_repo,
        moderation=moderation,
        async_session_factory=async_session_factory,
        app_share_origin=settings.sharecards_app_origin,
    )


__all__ = [
    "CARD_HEIGHT",
    "CARD_WIDTH",
    "InMemorySessionScoreRepository",
    "LocalFilesystemStorage",
    "PillowShareCardRenderer",
    "SessionCardData",
    "SessionScoreRepository",
    "ShareCardCaptionBlockedError",
    "ShareCardNotFoundError",
    "ShareCardRenderer",
    "ShareCardRendererError",
    "ShareCardService",
    "ShareCardStorage",
    "get_sharecard_service",
]
