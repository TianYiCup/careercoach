"""Unit tests for `InMemoryTurnRepository`.

PR 4b ships in-memory only. These tests double as the conformance
contract a SQL-backed implementation must also pass.
"""

from __future__ import annotations

from datetime import UTC, datetime

from app.agents.state import TurnScore, Verdict
from app.services.sessions.turn_repository import (
    CoachHintTrio,
    InMemoryTurnRepository,
    TurnRecord,
)


def _record(turn_id: str, session_id: str = "ses_aaaa1111") -> TurnRecord:
    return TurnRecord(
        turn_id=turn_id,
        session_id=session_id,
        user_content="老师我已经很努力了",
        opponent_reply="努力不是借口",
        coach_hint=CoachHintTrio(safe="先稳住", aggressive="直接顶回去", humor="说一句俏皮话"),
        turn_score=TurnScore(verdict=Verdict.GUOLU, rating=70),
        created_at=datetime(2026, 5, 13, 23, 0, tzinfo=UTC),
    )


async def test_list_for_session_returns_empty_when_no_turns_appended() -> None:
    repo = InMemoryTurnRepository()
    assert await repo.list_for_session("ses_unknown") == []


async def test_append_then_list_round_trip_preserves_order() -> None:
    repo = InMemoryTurnRepository()
    await repo.append(_record("t_001"))
    await repo.append(_record("t_002"))
    await repo.append(_record("t_003"))

    turns = await repo.list_for_session("ses_aaaa1111")
    assert [t.turn_id for t in turns] == ["t_001", "t_002", "t_003"]


async def test_list_for_session_is_isolated_per_session() -> None:
    repo = InMemoryTurnRepository()
    await repo.append(_record("t_a1", session_id="ses_a"))
    await repo.append(_record("t_b1", session_id="ses_b"))
    await repo.append(_record("t_a2", session_id="ses_a"))

    a = await repo.list_for_session("ses_a")
    b = await repo.list_for_session("ses_b")
    assert [t.turn_id for t in a] == ["t_a1", "t_a2"]
    assert [t.turn_id for t in b] == ["t_b1"]


async def test_list_for_session_returns_a_copy() -> None:
    """Mutating the returned list must not leak into the canonical store."""
    repo = InMemoryTurnRepository()
    await repo.append(_record("t_001"))

    returned = await repo.list_for_session("ses_aaaa1111")
    returned.clear()

    again = await repo.list_for_session("ses_aaaa1111")
    assert len(again) == 1
