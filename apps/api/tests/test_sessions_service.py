"""Behavioural tests for `SessionService` — the orchestrator that
connects sandbox session lifecycle to the sharecards data plane.

The single most important assertion in this file is the seam test:
`/end` must write the SessionCardData into the same SessionScoreRepo
that share-card service reads. If that link breaks, ending a session
no longer makes its card render — i.e. sharecards goes back to inert.
"""

from __future__ import annotations

import pytest
from app.schemas.sessions import CreateSessionRequest
from app.services.sessions import (
    InMemorySessionRepository,
    SessionAlreadyEndedError,
    SessionNotFoundError,
    SessionService,
)
from app.services.sharecards.session_score import InMemorySessionScoreRepository


def _service() -> tuple[SessionService, InMemorySessionScoreRepository]:
    score_repo = InMemorySessionScoreRepository()
    svc = SessionService(
        repository=InMemorySessionRepository(),
        score_repo=score_repo,
    )
    return svc, score_repo


def _request(scenario_id: str = "sc_001") -> CreateSessionRequest:
    return CreateSessionRequest(
        mode="sandbox",
        scenario_id=scenario_id,
        persona_id="p_hard",
        user_goal="保住周末，不得罪老板",
    )


async def test_create_session_returns_known_scenario_opening_line() -> None:
    svc, _ = _service()

    response = await svc.create_session(_request("sc_001"), user_id="u_test")

    assert response.session_id.startswith("ses_")
    assert len(response.session_id) > len("ses_")
    # sc_001 maps to the 周末加班谈判 seed.
    assert response.opening_line == "小林啊，这个周末项目得加个班，应该没问题吧？"


async def test_create_session_unknown_scenario_falls_back_to_generic_opening() -> None:
    """Sprint-1 catalog is sparse; unknown scenarios must still produce
    a session so we can demo against arbitrary ids while the catalog
    is growing."""
    svc, _ = _service()

    response = await svc.create_session(_request("sc_does_not_exist"), user_id="u_test")

    assert response.session_id.startswith("ses_")
    assert response.opening_line == "我们来聊聊吧。"


async def test_create_session_emits_unique_ids() -> None:
    svc, _ = _service()
    ids = {(await svc.create_session(_request(), user_id="u")).session_id for _ in range(8)}
    assert len(ids) == 8


async def test_end_session_returns_5_dim_score() -> None:
    svc, _ = _service()
    created = await svc.create_session(_request(), user_id="u_test")

    end_response = await svc.end_session(created.session_id, user_id="u_test")

    s = end_response.score
    # All five dimensions in range; result is one of the three literals.
    for dim in ("aura", "logic", "emotion", "professionalism", "goal_achieve"):
        v = getattr(s, dim)
        assert 0 <= v <= 10, f"{dim}={v} out of range"
    assert s.result in {"shenfeng", "guolu", "fanche"}
    assert s.highlights
    assert s.failures
    assert len(end_response.weakness_updates) >= 1


async def test_end_session_writes_card_data_to_shared_score_repo() -> None:
    """THE seam test — `/end` must populate the SessionScoreRepository
    that share-cards reads. Breaking this regresses sharecards to inert.
    """
    svc, score_repo = _service()
    created = await svc.create_session(_request("sc_001"), user_id="u_test")

    # Before /end, the score repo doesn't know about this session.
    assert await score_repo.get(created.session_id, user_id="u_test") is None

    await svc.end_session(created.session_id, user_id="u_test")

    card_data = await score_repo.get(created.session_id, user_id="u_test")
    assert card_data is not None
    # Scenario seed flows into the card payload — the renderer reads these.
    assert card_data.scenario_title == "周末加班谈判"
    assert card_data.persona_title == "强硬型 HR"
    # All five score dimensions made it through the adapter layer.
    assert 0 <= card_data.aura <= 10
    assert 0 <= card_data.goal_achieve <= 10
    assert card_data.result in {"shenfeng", "guolu", "fanche"}


async def test_end_session_marks_repository_status_ended() -> None:
    svc, _ = _service()
    created = await svc.create_session(_request(), user_id="u_test")

    await svc.end_session(created.session_id, user_id="u_test")

    record = await svc._repository.get(created.session_id)
    assert record is not None
    assert record.status == "ended"
    assert record.ended_at is not None


async def test_end_session_unknown_session_raises_not_found() -> None:
    svc, _ = _service()
    with pytest.raises(SessionNotFoundError):
        await svc.end_session("ses_never_created", user_id="u_test")


async def test_end_session_twice_raises_already_ended() -> None:
    svc, _ = _service()
    created = await svc.create_session(_request(), user_id="u_test")
    await svc.end_session(created.session_id, user_id="u_test")

    with pytest.raises(SessionAlreadyEndedError):
        await svc.end_session(created.session_id, user_id="u_test")
