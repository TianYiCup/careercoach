"""Failover-aware moderation backend with a local-dict red-line floor.

Tries the primary first (typically `AliyunTextModerationBackend`).
On `asyncio.TimeoutError` or `ModerationBackendError` we fall through
to the backup (typically `DictBackend`). Both arms must implement
`ModerationBackend`.

Red-line floor
--------------
A cloud `verdict=allow` is NOT trusted blindly. The Content-Moderation
2.0 chat scene has real coverage gaps — most importantly it does not
label self-harm — so on every cloud `allow` we re-check the backup dict
(the §3.0.5 red-line keyword list, proven ≥99.5% recall by the 200-sample
gate). If the backup flags a red-line the cloud missed, the backup wins.
The cloud still adds breadth on top of the floor: a cloud `block` /
`redirect` is trusted directly (it caught something the dict might not).

This is the "tighten the cascade" the earlier failover-only version
deferred to the 200-sample regression — enabling the live cloud scene
surfaced the self-harm gap, so the floor is now mandatory.
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
    """Primary → backup failover, with the backup as a red-line floor.

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
            primary = await asyncio.wait_for(
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
            # Primary never produced a Decision — backup decides outright.
            return await self._backup.evaluate(content, context)
        except ModerationBackendError as exc:
            logger.info(
                "moderation_failover",
                reason="backend_error",
                primary=self._primary.name,
                backup=self._backup.name,
                error=str(exc),
            )
            return await self._backup.evaluate(content, context)

        # Red-line floor: a cloud `allow` is re-checked against the local
        # dict so a red-line the cloud scene doesn't cover (self-harm) is
        # never let through. A cloud non-allow is trusted as-is.
        if primary.verdict == "allow":
            floor = await self._floor_check(content, context)
            if floor is not None:
                return floor
        return primary

    async def _floor_check(self, content: str, context: ModerationContext) -> Decision | None:
        """Return the backup Decision when it flags a red-line the cloud
        allowed, else None. A backup error here keeps the cloud `allow`
        (fail-open on the floor, not the whole request)."""
        try:
            backup = await self._backup.evaluate(content, context)
        except ModerationBackendError as exc:
            logger.warning(
                "moderation_floor_unavailable",
                primary=self._primary.name,
                backup=self._backup.name,
                error=str(exc),
            )
            return None
        if backup.verdict != "allow":
            logger.info(
                "moderation_redline_floor",
                primary=self._primary.name,
                backup=self._backup.name,
                verdict=backup.verdict,
                categories=list(backup.categories),
            )
            return backup
        return None
