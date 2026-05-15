"""Tests for `get_current_user_id` — the hard-auth FastAPI dependency.

Covers three branches:
  1. No Authorization header → 401 UNAUTHORIZED
  2. Bad / expired / tampered token → 401 UNAUTHORIZED
  3. Valid token → the `sub` claim

The previous soft transition (anonymous fallback) is gone. These tests
now lock in the deny-by-default contract — any regression that lets an
unauthenticated request through will fail loudly here.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest
from app.services.auth import (
    get_current_user_id,
    mint_token,
)
from fastapi import Depends, FastAPI
from httpx import ASGITransport, AsyncClient


@pytest.fixture
def app() -> FastAPI:
    """A tiny FastAPI app that just exposes the resolved user_id.

    Decoupling from `app.main` keeps these tests focused on the
    dependency wiring — no session / sharecard plumbing involved.
    """
    test_app = FastAPI()

    @test_app.get("/whoami")
    async def whoami(user_id: str = Depends(get_current_user_id)) -> dict[str, str]:
        return {"user_id": user_id}

    return test_app


@pytest.fixture
async def client(app: FastAPI) -> AsyncIterator[AsyncClient]:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


async def test_missing_authorization_header_rejected_with_401(
    client: AsyncClient,
) -> None:
    resp = await client.get("/whoami")
    assert resp.status_code == 401
    # WWW-Authenticate is RFC 6750 standard for Bearer-token 401s — a
    # well-behaved client uses it to know which scheme to retry under.
    assert resp.headers.get("www-authenticate") == "Bearer"


async def test_valid_bearer_token_resolves_to_user_id(client: AsyncClient) -> None:
    token = mint_token(
        user_id="u_abc",
        persona_type="intern",
        is_minor=False,
    )

    resp = await client.get("/whoami", headers={"Authorization": f"Bearer {token}"})

    assert resp.status_code == 200
    assert resp.json() == {"user_id": "u_abc"}


async def test_garbage_bearer_token_rejected_with_401(client: AsyncClient) -> None:
    """Tampered / expired / malformed token never reaches the route."""
    resp = await client.get(
        "/whoami",
        headers={"Authorization": "Bearer not-a-real-jwt"},
    )

    assert resp.status_code == 401
    assert resp.headers.get("www-authenticate") == "Bearer"


async def test_missing_bearer_prefix_rejected_with_401(
    client: AsyncClient,
) -> None:
    """HTTPBearer requires the `Bearer ` scheme; anything else is
    treated the same as a missing header — 401."""
    resp = await client.get(
        "/whoami",
        headers={"Authorization": "Token some-string"},
    )

    assert resp.status_code == 401
