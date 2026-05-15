"""Tests for `require_adult` dependency — the route-side minor gate.

No 副驾 routes exist yet, so we mount the dependency under a synthetic
test-only endpoint. This verifies:

  * adult JWT → 200 + user_id surfaced
  * minor JWT → 403 MINOR_FORBIDDEN (NOT 401 — auth succeeded; we're
    rejecting on policy)
  * no token → 401 (the prerequisite `get_current_user` fires first)

The synthetic endpoint is added inside each test via FastAPI's
`include_router` so the prod surface stays unchanged.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest
from app.main import app
from app.services.auth import CurrentUser, mint_token, require_adult
from fastapi import APIRouter, Depends
from httpx import ASGITransport, AsyncClient

# Mount a one-route APIRouter once at module load so all tests share
# it. Idempotent: re-importing doesn't double-register the path.
_test_router = APIRouter(prefix="/_test_minor_gate", tags=["test"])

# Module-level singleton dodges flake8-bugbear B008 (function call in
# argument default) — `Depends(...)` is the canonical FastAPI pattern,
# but tests/ isn't in the per-file ignore list.
_REQUIRE_ADULT = Depends(require_adult)


@_test_router.get("/adult-only")
async def _adult_only_endpoint(
    user: CurrentUser = _REQUIRE_ADULT,
) -> dict[str, object]:
    return {"user_id": user.user_id, "is_minor": user.is_minor}


# Register exactly once — `app.routes` already contains the prod paths,
# so we append our test path and let FastAPI's dedup handle re-import.
_already_registered = any(
    getattr(route, "path", None) == "/_test_minor_gate/adult-only" for route in app.routes
)
if not _already_registered:
    app.include_router(_test_router)


@pytest.fixture
async def client() -> AsyncIterator[AsyncClient]:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


async def test_adult_jwt_passes_through(client: AsyncClient) -> None:
    token = mint_token(
        user_id="u_adult",
        persona_type="intern",
        is_minor=False,
    )

    resp = await client.get(
        "/_test_minor_gate/adult-only",
        headers={"Authorization": f"Bearer {token}"},
    )

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["user_id"] == "u_adult"
    assert body["is_minor"] is False


async def test_minor_jwt_rejected_with_403_minor_forbidden(client: AsyncClient) -> None:
    """Critical: minors must not slip into copilot / future
    adult-only routes. 403 (not 401) — auth was fine, the policy denied."""
    token = mint_token(
        user_id="u_minor",
        persona_type="in_school",
        is_minor=True,
    )

    resp = await client.get(
        "/_test_minor_gate/adult-only",
        headers={"Authorization": f"Bearer {token}"},
    )

    assert resp.status_code == 403
    body = resp.json()
    assert body["code"] == "MINOR_FORBIDDEN"


async def test_no_token_still_returns_401_not_403(client: AsyncClient) -> None:
    """`require_adult` composes `get_current_user`, so the auth check
    fires before the minor check — must be 401, not 403, for an
    unauthenticated request."""
    resp = await client.get("/_test_minor_gate/adult-only")

    assert resp.status_code == 401
    assert resp.json()["code"] == "UNAUTHORIZED"
