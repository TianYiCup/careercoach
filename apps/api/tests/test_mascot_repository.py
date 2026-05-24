"""Repository tests for the mascot expression timeline (PRD §7.10)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from app.services.mascot.repository import (
    _MAX_MOMENTS_PER_SESSION,
    InMemoryMascotMomentRepository,
    MascotExpression,
    MascotMomentRecord,
)


def _moment(
    *,
    user_id: str = "u_1",
    session_id: str = "ses_1",
    turn_idx: int = 0,
    expression: MascotExpression = "thinking",
    at: datetime | None = None,
) -> MascotMomentRecord:
    return MascotMomentRecord(
        id=f"mm_{turn_idx}",
        user_id=user_id,
        session_id=session_id,
        turn_idx=turn_idx,
        expression=expression,
        at=at or datetime.now(UTC),
    )


async def test_append_then_list_round_trips() -> None:
    repo = InMemoryMascotMomentRepository()
    moment = _moment(turn_idx=0, expression="thinking")
    await repo.append(moment)
    assert await repo.list_for_session("u_1", "ses_1") == (moment,)


async def test_list_is_oldest_first() -> None:
    repo = InMemoryMascotMomentRepository()
    base = datetime(2026, 5, 22, 10, 0, 0, tzinfo=UTC)
    await repo.append(_moment(turn_idx=0, at=base))
    await repo.append(_moment(turn_idx=1, at=base + timedelta(seconds=5)))
    timeline = await repo.list_for_session("u_1", "ses_1")
    assert [m.turn_idx for m in timeline] == [0, 1]


async def test_unknown_session_yields_empty_tuple() -> None:
    repo = InMemoryMascotMomentRepository()
    assert await repo.list_for_session("u_1", "ses_never") == ()


async def test_timeline_is_isolated_per_user() -> None:
    """Two users logging the same session_id keep separate timelines —
    the store is keyed on (user_id, session_id)."""
    repo = InMemoryMascotMomentRepository()
    await repo.append(_moment(user_id="u_a", session_id="ses_shared"))
    await repo.append(_moment(user_id="u_b", session_id="ses_shared"))
    a = await repo.list_for_session("u_a", "ses_shared")
    b = await repo.list_for_session("u_b", "ses_shared")
    assert len(a) == 1 and a[0].user_id == "u_a"
    assert len(b) == 1 and b[0].user_id == "u_b"


async def test_timeline_is_capped_dropping_oldest() -> None:
    repo = InMemoryMascotMomentRepository()
    base = datetime(2026, 5, 22, tzinfo=UTC)
    overflow = _MAX_MOMENTS_PER_SESSION + 10
    for i in range(overflow):
        await repo.append(_moment(turn_idx=i, at=base + timedelta(seconds=i)))
    timeline = await repo.list_for_session("u_1", "ses_1")
    assert len(timeline) == _MAX_MOMENTS_PER_SESSION
    # The 10 oldest (turn_idx 0..9) are dropped; the newest survive.
    assert timeline[0].turn_idx == 10
    assert timeline[-1].turn_idx == overflow - 1
