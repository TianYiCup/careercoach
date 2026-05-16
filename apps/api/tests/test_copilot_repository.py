"""Unit tests for `InMemoryCopilotRepository`.

A-15 ships in-memory only. These tests double as the conformance
contract a SQL-backed implementation must also pass — the matching
Postgres test file lands when the docker stack is wired up in CI.
"""

from __future__ import annotations

from datetime import UTC, datetime

from app.services.copilot import (
    CopilotSessionRecord,
    InMemoryCopilotRepository,
)

_CREATED_AT = datetime(2026, 5, 16, 12, 0, tzinfo=UTC)
_CONNECTED_AT = datetime(2026, 5, 16, 12, 0, 5, tzinfo=UTC)
_ENDED_AT = datetime(2026, 5, 16, 12, 30, 0, tzinfo=UTC)


def _pending_record(copilot_id: str = "cop_aaaa1111") -> CopilotSessionRecord:
    """Mint a fresh copilot row in the `pending` state, mimicking
    what `POST /v1/copilot/sessions` writes before the WS endpoint
    (A-17) flips it."""
    return CopilotSessionRecord(
        copilot_id=copilot_id,
        user_id="u_demo",
        scenario_hint="interview salary negotiation",
        privacy_level="standard",
        status="pending",
        created_at=_CREATED_AT,
        connected_at=None,
        ended_at=None,
    )


async def test_create_then_get_round_trips_record() -> None:
    repo = InMemoryCopilotRepository()
    record = _pending_record()
    await repo.create(record)

    got = await repo.get("cop_aaaa1111")
    assert got == record


async def test_get_unknown_copilot_returns_none() -> None:
    repo = InMemoryCopilotRepository()
    assert await repo.get("cop_does_not_exist") is None


async def test_mark_connected_flips_status_and_timestamp() -> None:
    repo = InMemoryCopilotRepository()
    await repo.create(_pending_record())

    await repo.mark_connected("cop_aaaa1111", connected_at=_CONNECTED_AT)

    got = await repo.get("cop_aaaa1111")
    assert got is not None
    assert got.status == "connected"
    assert got.connected_at == _CONNECTED_AT
    # ended_at stays None until mark_ended runs.
    assert got.ended_at is None


async def test_mark_ended_flips_status_and_timestamp() -> None:
    """Idempotency-style sanity: mark_ended on a `pending` row (no
    intermediate `connected`) still flips status — the future WS
    handler may abort a `pending` session that never connected."""
    repo = InMemoryCopilotRepository()
    await repo.create(_pending_record())

    await repo.mark_ended("cop_aaaa1111", ended_at=_ENDED_AT)

    got = await repo.get("cop_aaaa1111")
    assert got is not None
    assert got.status == "ended"
    assert got.ended_at == _ENDED_AT


async def test_mark_connected_then_mark_ended_full_lifecycle() -> None:
    """The expected happy-path lifecycle: pending → connected → ended."""
    repo = InMemoryCopilotRepository()
    await repo.create(_pending_record())

    await repo.mark_connected("cop_aaaa1111", connected_at=_CONNECTED_AT)
    await repo.mark_ended("cop_aaaa1111", ended_at=_ENDED_AT)

    got = await repo.get("cop_aaaa1111")
    assert got is not None
    assert got.status == "ended"
    assert got.connected_at == _CONNECTED_AT
    assert got.ended_at == _ENDED_AT


async def test_mark_connected_on_unknown_copilot_is_silent_noop() -> None:
    """Repos are dumb stores — they do not raise on missing rows.
    Caller responsibility (the WS handler) is to fetch first and
    decide whether the copilot session is still valid."""
    repo = InMemoryCopilotRepository()

    await repo.mark_connected("cop_unknown", connected_at=_CONNECTED_AT)

    assert await repo.get("cop_unknown") is None


async def test_mark_ended_on_unknown_copilot_is_silent_noop() -> None:
    repo = InMemoryCopilotRepository()

    await repo.mark_ended("cop_unknown", ended_at=_ENDED_AT)

    assert await repo.get("cop_unknown") is None


async def test_high_privacy_level_round_trips() -> None:
    """`privacy_level=high` is the future on-device-ASR + redaction
    path (US-B3). A-15 just persists it for the future WS layer to
    route on; this test pins the round-trip."""
    repo = InMemoryCopilotRepository()
    record = CopilotSessionRecord(
        copilot_id="cop_high",
        user_id="u_demo",
        scenario_hint="interview salary negotiation",
        privacy_level="high",
        status="pending",
        created_at=_CREATED_AT,
        connected_at=None,
        ended_at=None,
    )
    await repo.create(record)

    got = await repo.get("cop_high")
    assert got is not None
    assert got.privacy_level == "high"
