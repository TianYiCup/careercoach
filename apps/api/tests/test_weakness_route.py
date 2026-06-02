"""HTTP tests for `GET /v1/users/me/weaknesses` and the session-end fold."""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterator
from datetime import UTC, datetime

import pytest
from app.main import app
from app.services.auth import mint_token
from app.services.sessions import get_session_service, get_turn_service
from app.services.sessions.service import _RESULT_WEAKNESS_TAG
from app.services.weakness import (
    InMemoryWeaknessRepository,
    WeaknessService,
    get_weakness_service,
)
from httpx import ASGITransport, AsyncClient

from tests.test_sessions_turns_route import _build_services


@pytest.fixture
def weakness_override() -> Iterator[InMemoryWeaknessRepository]:
    """Swap the real weakness service for one over a fresh in-memory
    repo so each test starts from an empty profile."""
    repo = InMemoryWeaknessRepository()
    app.dependency_overrides[get_weakness_service] = lambda: WeaknessService(repo=repo)
    try:
        yield repo
    finally:
        app.dependency_overrides.pop(get_weakness_service, None)


@pytest.fixture
async def client() -> AsyncIterator[AsyncClient]:
    """Default-authenticated client — `/me/weaknesses` is auth-gated."""
    token = mint_token(
        user_id="u_weakness_test",
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


async def test_get_weaknesses_empty_for_new_user(
    client: AsyncClient, weakness_override: InMemoryWeaknessRepository
) -> None:
    _ = weakness_override
    resp = await client.get("/v1/users/me/weaknesses")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["weaknesses"] == []
    # Recommendations fall back to catalog scenarios — non-empty.
    assert len(body["recommended_scenarios"]) >= 1


async def test_get_weaknesses_requires_auth() -> None:
    """No bearer token → hard 401."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        resp = await ac.get("/v1/users/me/weaknesses")
    assert resp.status_code == 401


async def test_get_weaknesses_lists_seeded_weaknesses_highest_frequency_first(
    client: AsyncClient, weakness_override: InMemoryWeaknessRepository
) -> None:
    now = datetime(2026, 5, 20, 8, 0, tzinfo=UTC)
    await weakness_override.increment(
        user_id="u_weakness_test", tag="过早让步", delta=2, now=now, fresh_id="wk_a"
    )
    await weakness_override.increment(
        user_id="u_weakness_test", tag="被绕话题", delta=5, now=now, fresh_id="wk_b"
    )
    resp = await client.get("/v1/users/me/weaknesses")
    tags = [w["tag"] for w in resp.json()["weaknesses"]]
    assert tags == ["被绕话题", "过早让步"]  # frequency-desc order


async def test_session_end_folds_weaknesses_into_profile(
    client: AsyncClient, weakness_override: InMemoryWeaknessRepository
) -> None:
    """End-to-end: a scored session (POST /sessions → /turns → /end)
    folds its per-tag weakness deltas into the profile that
    GET /me/weaknesses reads back."""
    _ = weakness_override
    session_svc, turn_svc = _build_services()
    app.dependency_overrides[get_session_service] = lambda: session_svc
    app.dependency_overrides[get_turn_service] = lambda: turn_svc
    try:
        create = await client.post(
            "/v1/sessions",
            json={
                "mode": "sandbox",
                "scenario_id": "sc_001",
                "persona_id": "p_hard",
                "user_goal": "保住周末",
            },
        )
        session_id = create.json()["session_id"]
        # One turn so the session has history — _derive_weakness_updates
        # only emits deltas for a non-empty session.
        turn = await client.post(
            f"/v1/sessions/{session_id}/turns",
            json={"content": "老板我周末有事"},
        )
        assert turn.status_code == 200
        end = await client.post(f"/v1/sessions/{session_id}/end")
        assert end.status_code == 200

        profile = await client.get("/v1/users/me/weaknesses")
        tags = [w["tag"] for w in profile.json()["weaknesses"]]
        # The stubbed session ends 路过 (judge VERDICT: guolu), so the
        # outcome-gated derivation folds in the 路过 weakness tag.
        assert _RESULT_WEAKNESS_TAG["guolu"] in tags
    finally:
        app.dependency_overrides.pop(get_session_service, None)
        app.dependency_overrides.pop(get_turn_service, None)
