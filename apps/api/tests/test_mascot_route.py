"""HTTP tests for the 教练 K mascot timeline endpoints (PRD §7.10)."""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterator

import pytest
from app.main import app
from app.services.auth import mint_token
from app.services.mascot import (
    InMemoryMascotMomentRepository,
    MascotService,
    get_mascot_service,
)
from httpx import ASGITransport, AsyncClient


@pytest.fixture
def mascot_override() -> Iterator[InMemoryMascotMomentRepository]:
    """Swap the real service for one over a fresh in-memory repo so each
    test starts from an empty timeline."""
    repo = InMemoryMascotMomentRepository()
    app.dependency_overrides[get_mascot_service] = lambda: MascotService(repo=repo)
    try:
        yield repo
    finally:
        app.dependency_overrides.pop(get_mascot_service, None)


def _client_for(user_id: str) -> AsyncClient:
    token = mint_token(
        user_id=user_id,
        persona_type="intern",
        is_minor=False,
        age_set=True,
    )
    return AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
        headers={"Authorization": f"Bearer {token}"},
    )


@pytest.fixture
async def client() -> AsyncIterator[AsyncClient]:
    """Default-authenticated client — the mascot endpoints are auth-gated."""
    async with _client_for("u_mascot_test") as ac:
        yield ac


async def test_log_moment_records_and_returns_it(
    client: AsyncClient, mascot_override: InMemoryMascotMomentRepository
) -> None:
    _ = mascot_override
    resp = await client.post(
        "/v1/mascot/log",
        json={"session_id": "ses_1", "turn_idx": 3, "expression": "thinking"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["turn_idx"] == 3
    assert body["expression"] == "thinking"
    assert body["at"]  # server-stamped ISO timestamp


async def test_log_then_get_round_trips_the_timeline(
    client: AsyncClient, mascot_override: InMemoryMascotMomentRepository
) -> None:
    _ = mascot_override
    await client.post(
        "/v1/mascot/log",
        json={"session_id": "ses_1", "turn_idx": 0, "expression": "thinking"},
    )
    await client.post(
        "/v1/mascot/log",
        json={"session_id": "ses_1", "turn_idx": 1, "expression": "shenfeng"},
    )
    resp = await client.get("/v1/mascot/expression", params={"session_id": "ses_1"})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["total"] == 2
    assert [m["expression"] for m in body["items"]] == ["thinking", "shenfeng"]


async def test_get_expression_empty_for_unlogged_session(
    client: AsyncClient, mascot_override: InMemoryMascotMomentRepository
) -> None:
    """A session with no moments returns an empty timeline, not a 404."""
    _ = mascot_override
    resp = await client.get("/v1/mascot/expression", params={"session_id": "ses_none"})
    assert resp.status_code == 200
    assert resp.json() == {"items": [], "total": 0}


async def test_get_expression_requires_session_id(
    client: AsyncClient, mascot_override: InMemoryMascotMomentRepository
) -> None:
    _ = mascot_override
    resp = await client.get("/v1/mascot/expression")
    assert resp.status_code == 422
    assert resp.json()["code"] == "VALIDATION_ERROR"


async def test_log_rejects_unknown_expression(
    client: AsyncClient, mascot_override: InMemoryMascotMomentRepository
) -> None:
    _ = mascot_override
    resp = await client.post(
        "/v1/mascot/log",
        json={"session_id": "ses_1", "turn_idx": 0, "expression": "ecstatic"},
    )
    assert resp.status_code == 422
    assert resp.json()["code"] == "VALIDATION_ERROR"


async def test_log_rejects_negative_turn_idx(
    client: AsyncClient, mascot_override: InMemoryMascotMomentRepository
) -> None:
    _ = mascot_override
    resp = await client.post(
        "/v1/mascot/log",
        json={"session_id": "ses_1", "turn_idx": -1, "expression": "thinking"},
    )
    assert resp.status_code == 422


async def test_mascot_endpoints_require_auth() -> None:
    """No bearer token → hard 401 on both routes."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        log = await ac.post(
            "/v1/mascot/log",
            json={"session_id": "ses_1", "turn_idx": 0, "expression": "thinking"},
        )
        get = await ac.get("/v1/mascot/expression", params={"session_id": "ses_1"})
    assert log.status_code == 401
    assert get.status_code == 401


async def test_timeline_is_isolated_per_user(
    mascot_override: InMemoryMascotMomentRepository,
) -> None:
    """User A's logged moments are invisible to user B, even for the
    same session_id — the timeline is keyed on the JWT `user_id`."""
    _ = mascot_override
    async with _client_for("u_owner") as owner:
        await owner.post(
            "/v1/mascot/log",
            json={"session_id": "ses_shared", "turn_idx": 0, "expression": "burning"},
        )
    async with _client_for("u_intruder") as intruder:
        resp = await intruder.get("/v1/mascot/expression", params={"session_id": "ses_shared"})
    assert resp.status_code == 200
    assert resp.json() == {"items": [], "total": 0}
