"""Service-layer tests for the new connect_session / end_session methods.

The WS endpoint tests in `test_copilot_ws.py` exercise these methods
end-to-end, but the close-code mapping (4404 / 4409) is sensitive to
which exception type is raised. These unit tests pin the exception
contract directly so a future refactor of the WS layer can rely on it.
"""

from __future__ import annotations

from datetime import UTC, datetime
from itertools import count

import pytest
from app.services.copilot import (
    CopilotService,
    CopilotSessionRecord,
    InMemoryCopilotRepository,
)
from app.services.copilot.service import (
    CopilotSessionNotFound,
    CopilotSessionUnavailable,
)

_WS_BASE_URL = "ws://test.local"
_SEED_CREATED_AT = datetime(2026, 5, 16, 11, 59, tzinfo=UTC)


def _build() -> tuple[CopilotService, InMemoryCopilotRepository]:
    repo = InMemoryCopilotRepository()
    id_counter = count(1)
    clock_counter = count(0)
    service = CopilotService(
        repo=repo,
        ws_base_url=_WS_BASE_URL,
        id_factory=lambda: f"cop_test{next(id_counter):010d}",
        clock=lambda: datetime(2026, 5, 16, 12, next(clock_counter), tzinfo=UTC),
    )
    return service, repo


def _pending(copilot_id: str) -> CopilotSessionRecord:
    return CopilotSessionRecord(
        copilot_id=copilot_id,
        user_id="u_demo",
        scenario_hint="hint",
        privacy_level="standard",
        status="pending",
        created_at=_SEED_CREATED_AT,
        connected_at=None,
        ended_at=None,
    )


async def test_connect_session_on_pending_returns_connected_record() -> None:
    service, repo = _build()
    await repo.create(_pending("cop_test0000000001"))

    got = await service.connect_session("cop_test0000000001")

    assert got.status == "connected"
    assert got.connected_at is not None
    # Repo state matches.
    persisted = await repo.get("cop_test0000000001")
    assert persisted is not None
    assert persisted.status == "connected"
    assert persisted.connected_at == got.connected_at


async def test_connect_session_on_unknown_raises_not_found() -> None:
    service, _ = _build()

    with pytest.raises(CopilotSessionNotFound):
        await service.connect_session("cop_does_not_exist")


async def test_connect_session_on_already_connected_raises_unavailable() -> None:
    """Re-using a copilot_id mid-flight must fail loudly. v0 has no
    reconnect — the WS layer maps this to close code 4409."""
    service, repo = _build()
    await repo.create(_pending("cop_test0000000001"))
    await service.connect_session("cop_test0000000001")

    with pytest.raises(CopilotSessionUnavailable):
        await service.connect_session("cop_test0000000001")


async def test_connect_session_on_already_ended_raises_unavailable() -> None:
    service, repo = _build()
    await repo.create(_pending("cop_test0000000001"))
    await service.connect_session("cop_test0000000001")
    await service.end_session("cop_test0000000001")

    with pytest.raises(CopilotSessionUnavailable):
        await service.connect_session("cop_test0000000001")


async def test_end_session_flips_status_and_ts() -> None:
    service, repo = _build()
    await repo.create(_pending("cop_test0000000001"))
    await service.connect_session("cop_test0000000001")

    await service.end_session("cop_test0000000001")

    persisted = await repo.get("cop_test0000000001")
    assert persisted is not None
    assert persisted.status == "ended"
    assert persisted.ended_at is not None
