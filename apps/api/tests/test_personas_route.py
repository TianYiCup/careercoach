"""HTTP-layer tests for `GET /v1/personas` (US-A2).

Verifies the route returns the locked `items` + `total` envelope, is
open (no auth, like `GET /v1/scenarios`), and — critically — never
leaks the internal `system_prompt` (PRD §6).
"""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest
from app.main import app
from httpx import ASGITransport, AsyncClient

_CARD_FIELDS = {"id", "name", "style", "age", "avatar", "background", "difficulty"}


@pytest.fixture
async def client() -> AsyncIterator[AsyncClient]:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


async def test_personas_route_returns_200_with_envelope(client: AsyncClient) -> None:
    resp = await client.get("/v1/personas")

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert isinstance(body["items"], list)
    assert body["total"] == len(body["items"])
    assert body["total"] == 4


async def test_personas_route_needs_no_auth(client: AsyncClient) -> None:
    """Static reference data — open like `GET /v1/scenarios`, no bearer."""
    resp = await client.get("/v1/personas")
    assert resp.status_code == 200


async def test_each_card_has_exactly_the_public_fields(client: AsyncClient) -> None:
    body = (await client.get("/v1/personas")).json()
    for item in body["items"]:
        assert set(item) == _CARD_FIELDS


async def test_route_never_leaks_the_system_prompt(client: AsyncClient) -> None:
    """PRD §6: `system_prompt` is the role-play seed — must not reach
    the client, by key or by raw content."""
    raw = (await client.get("/v1/personas")).text
    assert "system_prompt" not in raw
    assert "扮演用户对话练习中的对手" not in raw


async def test_cards_are_ordered_easy_to_hard(client: AsyncClient) -> None:
    body = (await client.get("/v1/personas")).json()
    difficulties = [item["difficulty"] for item in body["items"]]
    assert difficulties == sorted(difficulties)


async def test_includes_the_four_fixed_persona_ids(client: AsyncClient) -> None:
    body = (await client.get("/v1/personas")).json()
    assert {item["id"] for item in body["items"]} == {
        "p_mild",
        "p_hard",
        "p_pua",
        "p_sarcastic",
    }
