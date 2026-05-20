"""HTTP tests for `POST /v1/scenarios/custom` (R3-4)."""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterator

import pytest
from app.main import app
from app.services.auth import mint_token
from app.services.scenarios import CustomScenarioService, get_custom_scenario_service
from httpx import ASGITransport, AsyncClient

from tests.test_custom_scenario_service import _DESC, _allow_moderation, _blocking_moderation


def _install(service: CustomScenarioService) -> None:
    app.dependency_overrides[get_custom_scenario_service] = lambda: service


@pytest.fixture
def custom_allow() -> Iterator[CustomScenarioService]:
    """Override the custom-scenario service with one whose moderation
    passes everything — and a fresh per-test quota."""
    service = CustomScenarioService(moderation=_allow_moderation())
    _install(service)
    try:
        yield service
    finally:
        app.dependency_overrides.pop(get_custom_scenario_service, None)


@pytest.fixture
def custom_blocking() -> Iterator[None]:
    """Override with a service whose moderation blocks everything."""
    _install(CustomScenarioService(moderation=_blocking_moderation(verdict="block")))
    try:
        yield
    finally:
        app.dependency_overrides.pop(get_custom_scenario_service, None)


@pytest.fixture
async def client() -> AsyncIterator[AsyncClient]:
    """Default-authenticated client — `/scenarios/custom` is auth-gated."""
    token = mint_token(
        user_id="u_custom_test",
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


async def test_create_custom_scenario_returns_usable_scenario(
    client: AsyncClient, custom_allow: CustomScenarioService
) -> None:
    """A created scenario_id is immediately practisable: POST /sessions
    against it opens a session with the custom scenario's opening line
    (not the generic fallback)."""
    _ = custom_allow
    resp = await client.post("/v1/scenarios/custom", json={"description": _DESC})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["scenario_id"].startswith("sc_custom_")
    assert body["background"] == _DESC

    session = await client.post(
        "/v1/sessions",
        json={
            "mode": "sandbox",
            "scenario_id": body["scenario_id"],
            "persona_id": "p_custom",
            "user_goal": "练好这个场景",
        },
    )
    assert session.status_code == 200, session.text
    # Custom scenario resolved — its opening line, not the fallback's.
    assert session.json()["opening_line"] == "我们开始吧，你先说。"


async def test_create_custom_scenario_rejects_short_description(
    client: AsyncClient, custom_allow: CustomScenarioService
) -> None:
    _ = custom_allow
    resp = await client.post("/v1/scenarios/custom", json={"description": "太短了"})
    assert resp.status_code == 422
    assert resp.json()["code"] == "VALIDATION_ERROR"


async def test_create_custom_scenario_requires_auth() -> None:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        resp = await ac.post("/v1/scenarios/custom", json={"description": _DESC})
    assert resp.status_code == 401


async def test_create_custom_scenario_blocked_description_returns_400(
    client: AsyncClient, custom_blocking: None
) -> None:
    _ = custom_blocking
    resp = await client.post("/v1/scenarios/custom", json={"description": _DESC})
    assert resp.status_code == 400
    body = resp.json()
    assert body["code"] == "SCENARIO_BLOCKED"
    assert body["trace_id"]


async def test_create_custom_scenario_enforces_daily_quota(
    client: AsyncClient, custom_allow: CustomScenarioService
) -> None:
    """The 11th create in a day → 429 (PRD §7.12)."""
    _ = custom_allow
    for _ in range(10):
        ok = await client.post("/v1/scenarios/custom", json={"description": _DESC})
        assert ok.status_code == 200
    resp = await client.post("/v1/scenarios/custom", json={"description": _DESC})
    assert resp.status_code == 429
    assert resp.json()["code"] == "RATE_LIMIT_EXCEEDED"
