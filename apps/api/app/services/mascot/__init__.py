"""Mascot (教练 K expression timeline) service package — PRD §7.10.

Public surface:
  * `MascotMomentRecord` immutable record + `MascotExpression` Literal
  * `MascotMomentRepository` Protocol + `InMemoryMascotMomentRepository`
  * `MascotService` — `log_moment` / `get_timeline`
  * `get_mascot_repository()` + `get_mascot_service()` factory singletons

In-memory only — PRD §7.10 marks these moments "弱关联，可丢失", so
unlike vibe / streak there is no Postgres backend (and no settings
switch). A `PostgresMascotMomentRepository` can land behind the
Protocol later if the data ever needs to survive a restart.
"""

from functools import lru_cache

import structlog

from app.services.mascot.repository import (
    InMemoryMascotMomentRepository,
    MascotExpression,
    MascotMomentRecord,
    MascotMomentRepository,
)
from app.services.mascot.service import MascotService

logger = structlog.get_logger(__name__)


@lru_cache(maxsize=1)
def get_mascot_repository() -> MascotMomentRepository:
    """Process-wide mascot-moment repo singleton (in-memory; see the
    package docstring for why there is no Postgres backend)."""
    logger.info("mascot_repository_wired", backend="memory")
    return InMemoryMascotMomentRepository()


@lru_cache(maxsize=1)
def get_mascot_service() -> MascotService:
    """Default wiring for `GET /v1/mascot/expression` + `POST
    /v1/mascot/log`. Singleton so the read and write sides share one
    repo. Tests override via `app.dependency_overrides`."""
    repo = get_mascot_repository()
    logger.info("mascot_service_wired", repo=repo.__class__.__name__)
    return MascotService(repo=repo)


__all__ = [
    "InMemoryMascotMomentRepository",
    "MascotExpression",
    "MascotMomentRecord",
    "MascotMomentRepository",
    "MascotService",
    "get_mascot_repository",
    "get_mascot_service",
]
