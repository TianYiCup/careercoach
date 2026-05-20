"""Weakness service — the communication-weakness profile (PRD §7.7).

`apply_updates` folds a session's per-tag deltas into the profile;
`apply_safe` is the best-effort wrapper the session-end route uses so
a weakness-store hiccup never fails the scorecard return. `get_weaknesses`
is the read side behind `GET /v1/users/me/weaknesses`.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import structlog

from app.services.weakness.repository import WeaknessRecord, WeaknessRepository

logger = structlog.get_logger(__name__)


class WeaknessService:
    """Owns the per-user weakness profile."""

    def __init__(self, *, repo: WeaknessRepository) -> None:
        self._repo = repo

    async def apply_updates(
        self,
        *,
        user_id: str,
        tag_deltas: dict[str, int],
        now: datetime | None = None,
    ) -> None:
        """Fold `{tag: delta}` into the user's profile — one accumulating
        `increment` per tag. `now` is injectable for tests."""
        stamp = now or datetime.now(UTC)
        for tag, delta in tag_deltas.items():
            await self._repo.increment(
                user_id=user_id,
                tag=tag,
                delta=delta,
                now=stamp,
                fresh_id=f"wk_{uuid.uuid4().hex[:8]}",
            )

    async def apply_safe(
        self,
        *,
        user_id: str,
        tag_deltas: dict[str, int],
        now: datetime | None = None,
    ) -> None:
        """Best-effort `apply_updates` — swallows + logs any error so a
        weakness-store hiccup never fails the caller (the session-end
        route). Mirrors the streak `touch_safe` contract."""
        try:
            await self.apply_updates(user_id=user_id, tag_deltas=tag_deltas, now=now)
        except Exception:
            logger.exception("weakness_apply_failed", user_id=user_id)

    async def get_weaknesses(self, user_id: str) -> list[WeaknessRecord]:
        """The caller's tracked weaknesses, highest-frequency first.
        Empty list for a user who has never been scored."""
        return await self._repo.list_for_user(user_id)
