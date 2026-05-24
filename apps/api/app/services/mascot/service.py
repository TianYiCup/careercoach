"""Mascot service — 教练 K's per-session expression timeline (PRD §7.10).

`log_moment` appends one expression switch (behind `POST /v1/mascot/log`);
`get_timeline` reads a session's moments back (behind
`GET /v1/mascot/expression`) for Wrapped replay.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import structlog

from app.services.mascot.repository import (
    MascotExpression,
    MascotMomentRecord,
    MascotMomentRepository,
)

logger = structlog.get_logger(__name__)


class MascotService:
    """Owns 教练 K's expression timeline."""

    def __init__(self, *, repo: MascotMomentRepository) -> None:
        self._repo = repo

    async def log_moment(
        self,
        *,
        user_id: str,
        session_id: str,
        turn_idx: int,
        expression: MascotExpression,
    ) -> MascotMomentRecord:
        """Record one expression switch; `at` is stamped server-side in
        UTC (CLAUDE.md §6). `user_id` comes from the JWT, never the
        request body — that keys the timeline to its owner."""
        record = MascotMomentRecord(
            id=f"mm_{uuid.uuid4().hex[:8]}",
            user_id=user_id,
            session_id=session_id,
            turn_idx=turn_idx,
            expression=expression,
            at=datetime.now(UTC),
        )
        await self._repo.append(record)
        logger.info(
            "mascot_moment_logged",
            user_id=user_id,
            session_id=session_id,
            turn_idx=turn_idx,
            expression=expression,
        )
        return record

    async def get_timeline(
        self,
        *,
        user_id: str,
        session_id: str,
    ) -> tuple[MascotMomentRecord, ...]:
        """The caller's expression timeline for `session_id`, oldest
        first. A session with no logged moments yields an empty tuple —
        the route returns that as an empty timeline, not a 404."""
        return await self._repo.list_for_session(user_id, session_id)
