"""HTTP tests for `POST /v1/vibe/today` — daily mood check-in (PRD §7.11)."""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterator

import pytest
from app.main import app
from app.services.auth import mint_token
from app.services.vibe import InMemoryVibeRepository, VibeService, get_vibe_service
from httpx import ASGITransport, AsyncClient


@pytest.fixture
def vibe_override() -> Iterator[InMemoryVibeRepository]:
    """Swap the real service for one over a fresh in-memory repo so each
    test starts clean and never touches a DB."""
    repo = InMemoryVibeRepository()
    app.dependency_overrides[get_vibe_service] = lambda: VibeService(repo=repo)
    try:
        yield repo
    finally:
        app.dependency_overrides.pop(get_vibe_service, None)


@pytest.fixture
async def client() -> AsyncIterator[AsyncClient]:
    """Default-authenticated client — `/v1/vibe/today` is auth-gated."""
    token = mint_token(
        user_id="u_vibe_test",
        persona_type="intern",
        is_minor=False,
        age_set=True,
    )
    transport = ASGITransport(app=app)
    async with AsyncClient(
        transport=transport,
        base_url="http://test",
        headers={"Authorization": f"Bearer {token}"},
    ) as ac:
        yield ac


async def test_post_vibe_today_records_and_returns_mood(
    client: AsyncClient, vibe_override: InMemoryVibeRepository
) -> None:
    _ = vibe_override
    resp = await client.post("/v1/vibe/today", json={"vibe": "fire"})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["vibe"] == "fire"
    assert body["logged_date"]  # ISO date string


async def test_post_vibe_today_rejects_unknown_mood(
    client: AsyncClient, vibe_override: InMemoryVibeRepository
) -> None:
    _ = vibe_override
    resp = await client.post("/v1/vibe/today", json={"vibe": "ecstatic"})
    assert resp.status_code == 422
    assert resp.json()["code"] == "VALIDATION_ERROR"


async def test_post_vibe_today_requires_auth() -> None:
    """No bearer token → hard 401, the service is never reached."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        resp = await ac.post("/v1/vibe/today", json={"vibe": "fire"})
    assert resp.status_code == 401


async def test_post_vibe_today_recheckin_overwrites(
    client: AsyncClient, vibe_override: InMemoryVibeRepository
) -> None:
    """A second POST the same day overwrites the mood (one row per day)."""
    _ = vibe_override
    await client.post("/v1/vibe/today", json={"vibe": "tired"})
    resp = await client.post("/v1/vibe/today", json={"vibe": "excited"})
    assert resp.status_code == 200
    assert resp.json()["vibe"] == "excited"
