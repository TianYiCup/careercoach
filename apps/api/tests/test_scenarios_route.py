"""HTTP-layer tests for `GET /v1/scenarios`.

Verifies the route flips from 501 to 200, respects query filters, and
emits the OpenAPI-locked envelope (`items` + `total`).
"""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest
from app.main import app
from httpx import ASGITransport, AsyncClient


@pytest.fixture
async def client() -> AsyncIterator[AsyncClient]:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


async def test_scenarios_route_returns_200_with_envelope(client: AsyncClient) -> None:
    resp = await client.get("/v1/scenarios")

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert "items" in body
    assert "total" in body
    assert isinstance(body["items"], list)
    assert body["total"] == len(body["items"])
    assert body["total"] >= 3  # seeded baseline


async def test_scenarios_route_filters_by_category(client: AsyncClient) -> None:
    resp = await client.get("/v1/scenarios", params={"category": "intern"})

    assert resp.status_code == 200
    body = resp.json()
    assert all(item["category"] == "intern" for item in body["items"])


async def test_scenarios_route_filters_by_q(client: AsyncClient) -> None:
    resp = await client.get("/v1/scenarios", params={"q": "加班"})

    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] >= 1
    assert any("加班" in item["title"] for item in body["items"])


async def test_scenarios_route_rejects_invalid_category(client: AsyncClient) -> None:
    """`category` is a Pydantic literal; off-list values get 422."""
    resp = await client.get("/v1/scenarios", params={"category": "telepathy"})

    assert resp.status_code == 422


async def test_scenarios_route_rejects_overlong_q(client: AsyncClient) -> None:
    """`q` is bounded at 64 chars (schemas/scenarios.py)."""
    resp = await client.get("/v1/scenarios", params={"q": "x" * 65})

    assert resp.status_code == 422


async def test_scenarios_route_summary_strips_seed_fields(client: AsyncClient) -> None:
    """Make sure persona_title + opening_line don't leak — they're
    private to the session pipeline."""
    resp = await client.get("/v1/scenarios")

    assert resp.status_code == 200
    body = resp.json()
    for item in body["items"]:
        assert "persona_title" not in item
        assert "opening_line" not in item


async def test_scenarios_route_returns_empty_envelope_when_no_match(
    client: AsyncClient,
) -> None:
    resp = await client.get("/v1/scenarios", params={"q": "完全不存在的字符串"})

    assert resp.status_code == 200
    body = resp.json()
    assert body["items"] == []
    assert body["total"] == 0
