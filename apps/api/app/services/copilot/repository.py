"""Copilot-session persistence layer.

Two implementations: an in-memory store for unit tests + dev runs
without docker, and a SQLAlchemy-backed store for deployed envs.
The factory in `__init__.py` picks one via `settings.copilot_repo_backend`,
matching the `sessions_repo_backend` precedent.

Why this layer is separate from the route + service layers
----------------------------------------------------------
A-15 lands persistence + create_session *before* the ASR adapter
(A-16) and the WS endpoint (A-17). Splitting them keeps each PR
under the soft 800-LoC line and lets the WS handler import a stable
repository surface (`mark_connected` + `mark_ended` are here today,
even though only `create` / `get` get exercised by A-15's tests).

Read pattern
------------
A-15 only reads via `get(copilot_id)` for the future detail-fetch
route. A-17's WS endpoint will use it to load the session row on
handshake and check it's still `pending` / `connected`.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime
from typing import Literal, Protocol, runtime_checkable

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.models.copilot import CopilotSession

CopilotStatus = Literal["pending", "connected", "ended"]
PrivacyLevel = Literal["standard", "high"]


@dataclass(frozen=True)
class CopilotSessionRecord:
    """Immutable snapshot of one copilot session.

    Mirrors the `ReviewUploadRecord` pattern — tuple fields where
    relevant, frozen so callers can hand the same instance to multiple
    sinks without one mutating the other.

    `connected_at` / `ended_at` are None until the WS endpoint flips
    them in A-17.
    """

    copilot_id: str
    user_id: str
    scenario_hint: str
    privacy_level: PrivacyLevel
    status: CopilotStatus
    created_at: datetime
    connected_at: datetime | None
    ended_at: datetime | None


@runtime_checkable
class CopilotRepository(Protocol):
    """Persistence seam — both InMemory and Postgres impls below.

    `mark_connected` / `mark_ended` are silent no-ops on missing rows
    (same dumb-store contract as `ReviewRepository.update_result`) so
    the caller (the future WS handler) decides whether to fetch first.
    """

    async def create(self, record: CopilotSessionRecord) -> None: ...

    async def get(self, copilot_id: str) -> CopilotSessionRecord | None: ...

    async def mark_connected(
        self,
        copilot_id: str,
        *,
        connected_at: datetime,
    ) -> None: ...

    async def mark_ended(
        self,
        copilot_id: str,
        *,
        ended_at: datetime,
    ) -> None: ...


class InMemoryCopilotRepository:
    """Dict-backed store. Single uvicorn worker is the only writer in v0,
    so no locks. Reuses the immutable `CopilotSessionRecord` directly —
    the dataclass-replace pattern prevents accidental mutation."""

    def __init__(self) -> None:
        self._store: dict[str, CopilotSessionRecord] = {}

    async def create(self, record: CopilotSessionRecord) -> None:
        self._store[record.copilot_id] = record

    async def get(self, copilot_id: str) -> CopilotSessionRecord | None:
        return self._store.get(copilot_id)

    async def mark_connected(
        self,
        copilot_id: str,
        *,
        connected_at: datetime,
    ) -> None:
        existing = self._store.get(copilot_id)
        if existing is None:
            return
        self._store[copilot_id] = replace(
            existing,
            status="connected",
            connected_at=connected_at,
        )

    async def mark_ended(
        self,
        copilot_id: str,
        *,
        ended_at: datetime,
    ) -> None:
        existing = self._store.get(copilot_id)
        if existing is None:
            return
        self._store[copilot_id] = replace(
            existing,
            status="ended",
            ended_at=ended_at,
        )


class PostgresCopilotRepository:
    """SQLAlchemy-backed implementation.

    Each method opens a short transaction. `mark_*` methods use
    `session.get` (PK lookup) so missing rows return None and we
    silently return — caller (WS layer) decides whether to surface
    "session vanished" as a 4xx or just log.
    """

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def create(self, record: CopilotSessionRecord) -> None:
        async with self._session_factory() as session, session.begin():
            session.add(_record_to_model(record))

    async def get(self, copilot_id: str) -> CopilotSessionRecord | None:
        async with self._session_factory() as session:
            stmt = select(CopilotSession).where(CopilotSession.copilot_id == copilot_id)
            row = (await session.execute(stmt)).scalar_one_or_none()
            if row is None:
                return None
            return _model_to_record(row)

    async def mark_connected(
        self,
        copilot_id: str,
        *,
        connected_at: datetime,
    ) -> None:
        async with self._session_factory() as session, session.begin():
            row = await session.get(CopilotSession, copilot_id)
            if row is None:
                return
            row.status = "connected"
            row.connected_at = connected_at

    async def mark_ended(
        self,
        copilot_id: str,
        *,
        ended_at: datetime,
    ) -> None:
        async with self._session_factory() as session, session.begin():
            row = await session.get(CopilotSession, copilot_id)
            if row is None:
                return
            row.status = "ended"
            row.ended_at = ended_at


def _record_to_model(record: CopilotSessionRecord) -> CopilotSession:
    return CopilotSession(
        copilot_id=record.copilot_id,
        user_id=record.user_id,
        scenario_hint=record.scenario_hint,
        privacy_level=record.privacy_level,
        status=record.status,
        created_at=record.created_at,
        connected_at=record.connected_at,
        ended_at=record.ended_at,
    )


def _model_to_record(row: CopilotSession) -> CopilotSessionRecord:
    return CopilotSessionRecord(
        copilot_id=row.copilot_id,
        user_id=row.user_id,
        scenario_hint=row.scenario_hint,
        privacy_level=_coerce_privacy(row.privacy_level),
        status=_coerce_status(row.status),
        created_at=row.created_at,
        connected_at=row.connected_at,
        ended_at=row.ended_at,
    )


# The DB columns are plain String(16) so adding states / privacy
# levels never needs an ALTER TYPE migration. These narrowing helpers
# preserve the typed Literal at the boundary; an unexpected value
# (someone hand-edited the row) raises rather than silently
# corrupting downstream typing.
def _coerce_status(raw: str) -> CopilotStatus:
    if raw not in ("pending", "connected", "ended"):
        raise ValueError(f"unknown copilot status: {raw!r}")
    return raw  # type: ignore[return-value]


def _coerce_privacy(raw: str) -> PrivacyLevel:
    if raw not in ("standard", "high"):
        raise ValueError(f"unknown copilot privacy_level: {raw!r}")
    return raw  # type: ignore[return-value]
