"""Tests for `get_current_user_id` — the soft-auth FastAPI dependency.

Covers three branches:
  1. No Authorization header → ANONYMOUS_USER_ID
  2. Bad / expired / tampered token → ANONYMOUS_USER_ID
  3. Valid token → the `sub` claim

When the soft transition flips to hard 401, these tests get updated to
expect 401 in branches 1 and 2; that's the canary that catches the flip
landing without also updating callers.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest
from app.services.auth import (
    ANONYMOUS_USER_ID,
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


async def test_missing_authorization_header_falls_back_to_anonymous(
    client: AsyncClient,
) -> None:
    resp = await client.get("/whoami")
    assert resp.status_code == 200
    assert resp.json() == {"user_id": ANONYMOUS_USER_ID}


async def test_valid_bearer_token_resolves_to_user_id(client: AsyncClient) -> None:
    token = mint_token(
        user_id="u_abc",
        persona_type="intern",
        is_minor=False,
    )

    resp = await client.get("/whoami", headers={"Authorization": f"Bearer {token}"})

    assert resp.status_code == 200
    assert resp.json() == {"user_id": "u_abc"}


async def test_garbage_bearer_token_falls_back_to_anonymous(client: AsyncClient) -> None:
    """Soft mode: invalid token logs and degrades, doesn't 401.

    The follow-up PR flips this branch to 401; updating that assertion
    is the planned signal that the flip has landed."""
    resp = await client.get(
        "/whoami",
        headers={"Authorization": "Bearer not-a-real-jwt"},
    )

    assert resp.status_code == 200
    assert resp.json() == {"user_id": ANONYMOUS_USER_ID}


async def test_missing_bearer_prefix_falls_back_to_anonymous(
    client: AsyncClient,
) -> None:
    """HTTPBearer requires the `Bearer ` scheme; anything else is
    treated as no header in soft mode."""
    resp = await client.get(
        "/whoami",
        headers={"Authorization": "Token some-string"},
    )

    assert resp.status_code == 200
    assert resp.json() == {"user_id": ANONYMOUS_USER_ID}
