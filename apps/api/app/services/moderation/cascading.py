"""Failover-aware moderation backend.

Tries the primary first (typically `AliyunTextModerationBackend`).
On `asyncio.TimeoutError` or `ModerationBackendError` we fall through
to the backup (typically `DictBackend`). Both arms must implement
`ModerationBackend`.

We do NOT fall through on a valid `verdict=allow` from the cloud —
that's a real decision, even if the local dict would have flagged it.
The whole point of bringing in Aliyun is to trust its broader coverage
over a v0 hand-curated list. PR ④'s 200-sample regression measures
whether that policy holds; if it doesn't, we tighten the cascade then.
"""

from __future__ import annotations

import asyncio

import structlog

from app.schemas.moderation import ModerationContext
from app.services.moderation.backend import ModerationBackend, ModerationBackendError
from app.services.moderation.types import Decision

logger = structlog.get_logger(__name__)

DEFAULT_TIMEOUT_S = 0.8


class CascadingBackend:
    """Primary → backup failover.

    `name` advertises both arms so audit rows tell the story.
    Structurally implements `ModerationBackend`.
    """

    def __init__(
        self,
        *,
        primary: ModerationBackend,
        backup: ModerationBackend,
        timeout_s: float = DEFAULT_TIMEOUT_S,
    ) -> None:
        if timeout_s <= 0:
            raise ValueError("timeout_s must be positive")
        self._primary = primary
        self._backup = backup
        self._timeout_s = timeout_s
        self.name: str = f"cascading[{primary.name}>{backup.name}]"

    async def evaluate(self, content: str, context: ModerationContext) -> Decision:
        try:
            return await asyncio.wait_for(
                self._primary.evaluate(content, context),
                timeout=self._timeout_s,
            )
        except TimeoutError:
            logger.info(
                "moderation_failover",
                reason="timeout",
                primary=self._primary.name,
                backup=self._backup.name,
                timeout_s=self._timeout_s,
            )
        except ModerationBackendError as exc:
            logger.info(
                "moderation_failover",
                reason="backend_error",
                primary=self._primary.name,
                backup=self._backup.name,
                error=str(exc),
            )

        # Primary failed before producing a Decision — backup decides.
        # If the backup also errors out, we let it bubble up: the
        # service will catch and log, and the route maps to 502 via
        # FastAPI's default handler. v1 will add a hard `block` fallback
        # so red-line content never escapes when both arms collapse.
        return await self._backup.evaluate(content, context)
