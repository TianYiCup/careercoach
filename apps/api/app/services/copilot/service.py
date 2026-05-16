"""Copilot HTTP service — `CopilotService.create_session` orchestrator.

Why this layer exists between the route and the repo
-----------------------------------------------------
A-15 only ships `create_session`, but the same seam will carry
`mark_connected` / `mark_ended` calls from the future WS handler
(A-17), Langfuse trace start/finish (A-18+), and the ASR-adapter
selection that depends on `privacy_level` (A-16). Splitting now
matches the `SessionService` / `ReviewService` precedent.

WebSocket URL construction
--------------------------
A-15 doesn't ship a real WS endpoint — that lands in A-17. But the
`POST /v1/copilot/sessions` response must already include the
`ws_url` so B's client can prepare the connection state. We build
the URL from `settings.copilot_ws_base_url` + a fixed path; once
A-17 lands the endpoint at that path, no client-side change is
required.

The URL shape is `{base}/v1/copilot/sessions/{copilot_id}/stream` so
the WS handler's path-parameter shape mirrors the HTTP detail route
that the future `GET /v1/copilot/sessions/{copilot_id}` will use.
"""

from __future__ import annotations

import secrets
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from typing import Protocol

import structlog

from app.services.copilot.repository import (
    CopilotRepository,
    CopilotSessionRecord,
    PrivacyLevel,
)


class CopilotSessionNotFound(Exception):
    """Raised by `connect_session` when the copilot_id is unknown.
    The WS layer maps this to close code 4404."""


class CopilotSessionUnavailable(Exception):
    """Raised by `connect_session` when the row exists but is not in
    `pending` state — already-connected or already-ended. v0 has no
    reconnect; a dropped WS forces a fresh POST. The WS layer maps
    this to close code 4409."""


logger = structlog.get_logger(__name__)

# `cop_` prefix mirrors the example in `schemas/copilot.py` and keeps
# IDs visually distinguishable from `s_` (session) / `up_` (review
# upload) / `u_` (user) ids in trace logs and ad-hoc DB queries.
_COPILOT_ID_PREFIX = "cop_"
_COPILOT_ID_HEX_LEN = 16


class _IdFactory(Protocol):
    def __call__(self) -> str: ...


class _Clock(Protocol):
    def __call__(self) -> datetime: ...


def _default_id_factory() -> str:
    return _COPILOT_ID_PREFIX + secrets.token_hex(_COPILOT_ID_HEX_LEN // 2)


def _default_clock() -> datetime:
    return datetime.now(UTC)


@dataclass(frozen=True)
class CreateSessionResult:
    """Service-level output of `create_session`. The route maps this
    into `CreateCopilotSessionResponse(copilot_id, ws_url)`."""

    record: CopilotSessionRecord
    ws_url: str


class CopilotService:
    """Stateless helper — every request gets its own service call but
    the underlying `repo` singleton is shared."""

    def __init__(
        self,
        *,
        repo: CopilotRepository,
        ws_base_url: str,
        id_factory: _IdFactory | None = None,
        clock: _Clock | None = None,
    ) -> None:
        self._repo = repo
        # Stripped of trailing slash so URL construction is unambiguous.
        self._ws_base_url = ws_base_url.rstrip("/")
        self._id_factory: _IdFactory = id_factory or _default_id_factory
        self._clock: _Clock = clock or _default_clock

    async def create_session(
        self,
        *,
        scenario_hint: str,
        privacy_level: PrivacyLevel,
        user_id: str,
    ) -> CreateSessionResult:
        """Mint copilot_id → persist `pending` row → build ws_url → return.

        No LLM call, no moderation here — the copilot mode is
        adult-only (route enforces `require_adult`) and the actual
        audio + transcript moderation lives in the WS handler (A-17+).
        """
        copilot_id = self._id_factory()
        created_at = self._clock()
        record = CopilotSessionRecord(
            copilot_id=copilot_id,
            user_id=user_id,
            scenario_hint=scenario_hint,
            privacy_level=privacy_level,
            status="pending",
            created_at=created_at,
            connected_at=None,
            ended_at=None,
        )
        await self._repo.create(record)
        ws_url = f"{self._ws_base_url}/v1/copilot/sessions/{copilot_id}/stream"
        logger.info(
            "copilot_session_created",
            copilot_id=copilot_id,
            user_id=user_id,
            privacy_level=privacy_level,
        )
        return CreateSessionResult(record=record, ws_url=ws_url)

    async def connect_session(self, copilot_id: str) -> CopilotSessionRecord:
        """Flip a pending row to `connected` and return the updated record.

        Used by the WS handler on accept. Raises:
          * `CopilotSessionNotFound` — id was never POSTed (or the row
            was purged); WS closes 4404.
          * `CopilotSessionUnavailable` — row exists but already
            connected or ended; WS closes 4409.

        The fetch + transition is *not* atomic against a concurrent
        second connect — the in-memory repo is single-threaded per
        worker, and the postgres impl serializes via row-level lock on
        the next call to `mark_connected`. A concurrent double-connect
        in postgres can race; we accept that for v0 because the
        copilot_id has 64 bits of entropy and the only legitimate
        client is the one that just got the POST response.
        """
        record = await self._repo.get(copilot_id)
        if record is None:
            logger.info("copilot_connect_unknown", copilot_id=copilot_id)
            raise CopilotSessionNotFound(copilot_id)
        if record.status != "pending":
            logger.info(
                "copilot_connect_unavailable",
                copilot_id=copilot_id,
                current_status=record.status,
            )
            raise CopilotSessionUnavailable(copilot_id, record.status)
        connected_at = self._clock()
        await self._repo.mark_connected(copilot_id, connected_at=connected_at)
        logger.info(
            "copilot_session_connected",
            copilot_id=copilot_id,
            user_id=record.user_id,
        )
        return replace(record, status="connected", connected_at=connected_at)

    async def end_session(self, copilot_id: str) -> None:
        """Flip a connected row to `ended`. Idempotent — calling it on
        an already-ended row is a no-op (the repo's mark_ended is a
        silent no-op on missing rows; on present rows it just
        overwrites status + ended_at, so a duplicate end is harmless).

        Called from the WS handler's `finally` so a normal close, an
        unexpected exception, or a transport drop all converge on the
        same persistence outcome. The handler only invokes this if
        `connect_session` succeeded — see the route for the gating.
        """
        ended_at = self._clock()
        await self._repo.mark_ended(copilot_id, ended_at=ended_at)
        logger.info("copilot_session_ended", copilot_id=copilot_id)
