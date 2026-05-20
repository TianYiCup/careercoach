"""HTTP tests for `GET /v1/streak` and the `POST /v1/sessions` streak touch."""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterator
from datetime import date

import pytest
from app.main import app
from app.services.auth import mint_token
from app.services.streak import (
    InMemoryStreakRepository,
    StreakRecord,
    StreakService,
    get_streak_service,
)
from httpx import ASGITransport, AsyncClient


@pytest.fixture
def streak_override() -> Iterator[InMemoryStreakRepository]:
    """Swap the real streak service for one over a fresh in-memory repo
    so each test starts from a clean (empty) streak store."""
    repo = InMemoryStreakRepository()
    app.dependency_overrides[get_streak_service] = lambda: StreakService(repo=repo)
    try:
        yield repo
    finally:
        app.dependency_overrides.pop(get_streak_service, None)


@pytest.fixture
async def client() -> AsyncIterator[AsyncClient]:
    """Default-authenticated client — `/v1/streak` is auth-gated."""
    token = mint_token(
        user_id="u_streak_test",
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


async def test_get_streak_zero_for_user_who_never_practised(
    client: AsyncClient, streak_override: InMemoryStreakRepository
) -> None:
    _ = streak_override
    resp = await client.get("/v1/streak")
    assert resp.status_code == 200, resp.text
    assert resp.json() == {"current_days": 0, "max_days": 0}


async def test_get_streak_requires_auth() -> None:
    """No bearer token → hard 401."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        resp = await ac.get("/v1/streak")
    assert resp.status_code == 401


async def test_get_streak_reflects_a_seeded_streak(
    client: AsyncClient, streak_override: InMemoryStreakRepository
) -> None:
    await streak_override.upsert(
        StreakRecord(
            user_id="u_streak_test",
            current_days=7,
            max_days=12,
            last_active_date=date(2026, 5, 20),
        )
    )
    resp = await client.get("/v1/streak")
    assert resp.json() == {"current_days": 7, "max_days": 12}


async def test_post_sessions_advances_the_streak(
    client: AsyncClient, streak_override: InMemoryStreakRepository
) -> None:
    """Starting a session counts as practising today — `POST /v1/sessions`
    touches the streak, so a fresh user goes 0 → 1 end-to-end."""
    _ = streak_override
    before = await client.get("/v1/streak")
    assert before.json()["current_days"] == 0

    create = await client.post(
        "/v1/sessions",
        json={
            "mode": "sandbox",
            "scenario_id": "sc_001",
            "persona_id": "p_hard",
            "user_goal": "保住周末",
        },
    )
    assert create.status_code == 200, create.text

    after = await client.get("/v1/streak")
    assert after.json()["current_days"] == 1
