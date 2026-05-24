"""Unit tests for `MascotService` (PRD §7.10)."""

from __future__ import annotations

from app.services.mascot import InMemoryMascotMomentRepository, MascotService


def _service() -> MascotService:
    return MascotService(repo=InMemoryMascotMomentRepository())


async def test_log_moment_returns_record_with_generated_fields() -> None:
    record = await _service().log_moment(
        user_id="u_1",
        session_id="ses_1",
        turn_idx=2,
        expression="shenfeng",
    )
    assert record.id.startswith("mm_")
    assert record.user_id == "u_1"
    assert record.expression == "shenfeng"
    assert record.at.tzinfo is not None  # server stamps a UTC-aware time


async def test_logged_moments_are_readable_in_order() -> None:
    service = _service()
    await service.log_moment(user_id="u_1", session_id="ses_1", turn_idx=0, expression="thinking")
    await service.log_moment(user_id="u_1", session_id="ses_1", turn_idx=1, expression="burning")
    timeline = await service.get_timeline(user_id="u_1", session_id="ses_1")
    assert [m.expression for m in timeline] == ["thinking", "burning"]


async def test_get_timeline_is_empty_for_unlogged_session() -> None:
    assert await _service().get_timeline(user_id="u_1", session_id="ses_none") == ()
