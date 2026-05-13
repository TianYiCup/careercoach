"""HTTP-layer tests for `POST /v1/sessions` and `POST /v1/sessions/{id}/end`.

We override `get_session_service` with a fresh in-memory wiring per
test so the singleton state from a previous test never leaks across
the boundary.

`/turns` stays 501 in PR 4a — the test for that ensures the contract
is "still 501" rather than accidentally working with a stale handler.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterator

import pytest
from app.main import app
from app.services.sessions import (
    InMemorySessionRepository,
    SessionService,
    get_session_service,
)
from app.services.sharecards.session_score import (
    InMemorySessionScoreRepository,
    get_session_score_repository,
)
from httpx import ASGITransport, AsyncClient


@pytest.fixture
def service_override() -> Iterator[InMemorySessionScoreRepository]:
    """Wire a fresh SessionService backed by isolated in-memory stores.

    The score repo is also overridden so a sharecards route call could
    read what `/end` wrote — covering the cross-service seam end-to-end
    is exercised separately in test_sessions_service.py.
    """
    score_repo = InMemorySessionScoreRepository()
    service = SessionService(
        repository=InMemorySessionRepository(),
        score_repo=score_repo,
    )
    app.dependency_overrides[get_session_service] = lambda: service
    app.dependency_overrides[get_session_score_repository] = lambda: score_repo
    try:
        yield score_repo
    finally:
        app.dependency_overrides.pop(get_session_service, None)
        app.dependency_overrides.pop(get_session_score_repository, None)


@pytest.fixture
async def client(
    service_override: InMemorySessionScoreRepository,
) -> AsyncIterator[AsyncClient]:
    _ = service_override
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


async def test_create_session_returns_200_with_session_id_and_opening_line(
    client: AsyncClient,
) -> None:
    resp = await client.post(
        "/v1/sessions",
        json={
            "mode": "sandbox",
            "scenario_id": "sc_001",
            "persona_id": "p_hard",
            "user_goal": "保住周末",
        },
    )

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["session_id"].startswith("ses_")
    assert body["opening_line"]  # non-empty
    assert isinstance(body["opening_line"], str)


async def test_create_session_validates_required_fields(client: AsyncClient) -> None:
    resp = await client.post("/v1/sessions", json={"mode": "sandbox"})
    assert resp.status_code == 422
    body = resp.json()
    assert body["code"] == "VALIDATION_ERROR"


async def test_create_session_validates_user_goal_length(client: AsyncClient) -> None:
    """user_goal is capped at 200 chars per schemas/sessions.py."""
    resp = await client.post(
        "/v1/sessions",
        json={
            "mode": "sandbox",
            "scenario_id": "sc_001",
            "persona_id": "p_hard",
            "user_goal": "x" * 201,
        },
    )
    assert resp.status_code == 422


async def test_create_session_rejects_invalid_mode(client: AsyncClient) -> None:
    resp = await client.post(
        "/v1/sessions",
        json={
            "mode": "telepathy",  # not in the Literal union
            "scenario_id": "sc_001",
            "persona_id": "p_hard",
            "user_goal": "试试",
        },
    )
    assert resp.status_code == 422


async def test_end_session_route_returns_score_with_5_dims(client: AsyncClient) -> None:
    created = await client.post(
        "/v1/sessions",
        json={
            "mode": "sandbox",
            "scenario_id": "sc_001",
            "persona_id": "p_hard",
            "user_goal": "保住周末",
        },
    )
    session_id = created.json()["session_id"]

    resp = await client.post(f"/v1/sessions/{session_id}/end")

    assert resp.status_code == 200, resp.text
    body = resp.json()
    for dim in ("aura", "logic", "emotion", "professionalism", "goal_achieve"):
        assert 0 <= body["score"][dim] <= 10
    assert body["score"]["result"] in {"shenfeng", "guolu", "fanche"}
    assert isinstance(body["weakness_updates"], list)


async def test_end_session_route_returns_404_for_unknown_session(
    client: AsyncClient,
) -> None:
    resp = await client.post("/v1/sessions/ses_never_existed/end")
    assert resp.status_code == 404
    body = resp.json()
    assert body["code"] == "NOT_FOUND"


async def test_end_session_route_returns_409_when_ended_twice(client: AsyncClient) -> None:
    created = await client.post(
        "/v1/sessions",
        json={
            "mode": "sandbox",
            "scenario_id": "sc_001",
            "persona_id": "p_hard",
            "user_goal": "保住周末",
        },
    )
    session_id = created.json()["session_id"]
    first = await client.post(f"/v1/sessions/{session_id}/end")
    assert first.status_code == 200

    second = await client.post(f"/v1/sessions/{session_id}/end")
    assert second.status_code == 409
    assert second.json()["code"] == "ALREADY_ENDED"


# PR 4b removed the /turns 501 canary — SSE-driven /turns coverage
# lives in test_sessions_turns_route.py.
