"""HTTP tests for `GET /v1/users/me/profile` (Character Engine L5)."""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterator
from datetime import UTC, datetime

import pytest
from app.main import app
from app.services.auth import mint_token
from app.services.profile import (
    InMemoryProfileRepository,
    ProfileService,
    get_profile_service,
)
from httpx import ASGITransport, AsyncClient


@pytest.fixture
def profile_override() -> Iterator[InMemoryProfileRepository]:
    """Swap the real profile service for one over a fresh in-memory repo
    so each test starts from an empty profile."""
    repo = InMemoryProfileRepository()
    app.dependency_overrides[get_profile_service] = lambda: ProfileService(repo=repo)
    try:
        yield repo
    finally:
        app.dependency_overrides.pop(get_profile_service, None)


@pytest.fixture
async def client() -> AsyncIterator[AsyncClient]:
    token = mint_token(
        user_id="u_profile_test",
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


async def test_profile_empty_for_new_user(
    client: AsyncClient, profile_override: InMemoryProfileRepository
) -> None:
    _ = profile_override
    resp = await client.get("/v1/users/me/profile")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["stats"] == []
    assert body["total_observations"] == 0
    assert body["overrelied_strategy"] is None


async def test_profile_requires_auth() -> None:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        resp = await ac.get("/v1/users/me/profile")
    assert resp.status_code == 401


async def test_profile_reports_stats_win_rate_and_crutch(
    client: AsyncClient, profile_override: InMemoryProfileRepository
) -> None:
    now = datetime(2026, 5, 28, 8, 0, tzinfo=UTC)
    for effect in ("poor", "poor", "poor", "good"):
        await profile_override.record(
            user_id="u_profile_test",
            strategy="placate",
            effect=effect,
            now=now,
            fresh_id=f"us_{effect}",
        )
    await profile_override.record(
        user_id="u_profile_test",
        strategy="direct",
        effect="good",
        now=now,
        fresh_id="us_d",
    )

    resp = await client.get("/v1/users/me/profile")
    body = resp.json()

    assert body["total_observations"] == 5
    assert body["overrelied_strategy"] == "placate"
    # Highest-count strategy first.
    assert body["stats"][0]["strategy"] == "placate"
    assert body["stats"][0]["count"] == 4
    assert body["stats"][0]["win_rate"] == 0.25
