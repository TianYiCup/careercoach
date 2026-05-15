"""Audit-log sink for moderation Decisions (PRD §6.2).

Every call to `ModerationService.check` produces a `ModerationEvent`
row so we can:
  * replay decisions during red-line incident review
  * compute per-category recall / FP rate offline (PR ④ runs against
    this table when scoring the 200-sample regression set)
  * surface "trace_id 1234 was redirected because…" in support

We persist a **hash** of the content rather than the raw text, so the
audit log itself can't leak red-line phrases. Raw retention, if we
ever add it, will be a separate opt-in table behind a privacy review.
"""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from typing import Protocol
from uuid import uuid4

import structlog
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.models.moderation_event import ModerationEvent
from app.schemas.moderation import ModerationCheckRequest
from app.services.moderation.types import Decision

logger = structlog.get_logger(__name__)


class ModerationEventSink(Protocol):
    """Async sink that records one moderation decision.

    `user_id` is a separate kwarg (not on `request`) — it's always the
    JWT-derived acting user, never the payload's self-reported one. See
    `app.schemas.moderation.ModerationCheckRequest` for the rationale.
    """

    async def record(
        self,
        *,
        request: ModerationCheckRequest,
        user_id: str,
        decision: Decision,
        backend_name: str,
        trace_id: str,
    ) -> None: ...


class DbEventSink:
    """Default sink — writes one row per decision to `moderation_events`."""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def record(
        self,
        *,
        request: ModerationCheckRequest,
        user_id: str,
        decision: Decision,
        backend_name: str,
        trace_id: str,
    ) -> None:
        event = ModerationEvent(
            id=uuid4(),
            user_id=user_id,
            session_id=request.session_id,
            content_hash=_hash_content(request.content),
            content_length=len(request.content),
            context=request.context,
            verdict=decision.verdict,
            categories=list(decision.categories),
            score=decision.score,
            backend=backend_name,
            trace_id=trace_id,
            created_at=datetime.now(UTC),
        )
        async with self._session_factory() as session:
            session.add(event)
            await session.commit()


class LogOnlyEventSink:
    """Drop-in sink used when the DB isn't reachable (dev, contract tests).

    Emits a structured log line so the decision is still observable. We
    explicitly avoid `print()` — structlog routes through stdlib
    `logging` so collectors pick it up like everything else.
    """

    async def record(
        self,
        *,
        request: ModerationCheckRequest,
        user_id: str,
        decision: Decision,
        backend_name: str,
        trace_id: str,
    ) -> None:
        logger.info(
            "moderation_decision",
            verdict=decision.verdict,
            score=decision.score,
            categories=list(decision.categories),
            backend=backend_name,
            context=request.context,
            content_hash=_hash_content(request.content),
            content_length=len(request.content),
            trace_id=trace_id,
            user_id=user_id,
            session_id=request.session_id,
        )


def _hash_content(content: str) -> str:
    """SHA-256 hex of the UTF-8 bytes — stable across processes."""
    return hashlib.sha256(content.encode("utf-8")).hexdigest()
