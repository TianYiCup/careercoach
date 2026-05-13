"""Unit tests for `InMemorySessionRepository` — the SessionService's
persistence seam. SQL-backed variant is added in PR 4b; these tests
double as the conformance contract that the SQL impl must also pass.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from app.services.sessions.repository import (
    InMemorySessionRepository,
    SessionRecord,
)


def _record(**overrides: object) -> SessionRecord:
    base = SessionRecord(
        session_id="ses_aaaa1111",
        user_id="anonymous",
        mode="sandbox",
        scenario_id="sc_001",
        persona_id="p_hard",
        user_goal="保住周末",
        status="active",
        created_at=datetime(2026, 5, 13, 23, 0, tzinfo=UTC),
    )
    return SessionRecord(**{**base.__dict__, **overrides})


async def test_get_returns_none_for_unknown_session() -> None:
    repo = InMemorySessionRepository()
    assert await repo.get("ses_unknown") is None


async def test_save_then_get_round_trip() -> None:
    repo = InMemorySessionRepository()
    record = _record()
    await repo.save(record)
    assert await repo.get(record.session_id) == record


async def test_save_is_idempotent_overwrite() -> None:
    """v0 doesn't enforce uniqueness — second save replaces. Sprint-2
    SQL impl will add a server-side unique constraint."""
    repo = InMemorySessionRepository()
    await repo.save(_record(user_goal="原 goal"))
    await repo.save(_record(user_goal="新 goal"))
    fetched = await repo.get("ses_aaaa1111")
    assert fetched is not None
    assert fetched.user_goal == "新 goal"


async def test_mark_ended_flips_status_and_stamps_time() -> None:
    repo = InMemorySessionRepository()
    await repo.save(_record())
    ended_at = datetime(2026, 5, 13, 23, 5, tzinfo=UTC)

    updated = await repo.mark_ended("ses_aaaa1111", ended_at=ended_at)

    assert updated.status == "ended"
    assert updated.ended_at == ended_at
    # The stored record reflects the new state, not a stale active row.
    refetched = await repo.get("ses_aaaa1111")
    assert refetched is not None
    assert refetched.status == "ended"


async def test_mark_ended_raises_for_unknown_session() -> None:
    repo = InMemorySessionRepository()
    with pytest.raises(KeyError):
        await repo.mark_ended("ses_unknown", ended_at=datetime.now(UTC))


async def test_session_record_is_immutable() -> None:
    """`SessionRecord` is frozen — required so callers can't accidentally
    drift the stored object after save."""
    from dataclasses import FrozenInstanceError

    record = _record()
    with pytest.raises(FrozenInstanceError):
        record.status = "ended"  # type: ignore[misc]
