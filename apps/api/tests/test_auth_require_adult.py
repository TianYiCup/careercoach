"""Tests for `require_adult` dependency — the route-side minor gate.

`require_adult` chains `require_age_set` → `get_current_user`, so the
ordered rejection ladder is:

  * no token                  → 401 UNAUTHORIZED
  * token, age unset          → 403 AGE_REQUIRED
  * token, age set, minor     → 403 MINOR_FORBIDDEN
  * token, age set, adult     → 200 (pass-through)

The 副驾 stub route mounts this dependency directly; this file
verifies the gate behavior in isolation via a synthetic test
endpoint (the prod copilot route gets its own integration tests).
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
        age_set=True,
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
        age_set=True,
    )

    resp = await client.get(
        "/_test_minor_gate/adult-only",
        headers={"Authorization": f"Bearer {token}"},
    )

    assert resp.status_code == 403
    body = resp.json()
    assert body["code"] == "MINOR_FORBIDDEN"


async def test_no_token_still_returns_401_not_403(client: AsyncClient) -> None:
    """`require_adult` composes `get_current_user` through `require_age_set`,
    so the auth check fires first. Must be 401, not 403, for an
    unauthenticated request."""
    resp = await client.get("/_test_minor_gate/adult-only")

    assert resp.status_code == 401
    assert resp.json()["code"] == "UNAUTHORIZED"


async def test_age_unset_returns_403_age_required(client: AsyncClient) -> None:
    """Compliance hole closer: a user who never declared their birth
    year defaults to `is_minor=False` server-side, which would
    *otherwise* let them slip into 副驾. The chain through
    `require_age_set` forces an explicit age declaration first."""
    token = mint_token(
        user_id="u_unset",
        persona_type="intern",
        is_minor=False,
        age_set=False,
    )

    resp = await client.get(
        "/_test_minor_gate/adult-only",
        headers={"Authorization": f"Bearer {token}"},
    )

    assert resp.status_code == 403
    body = resp.json()
    assert body["code"] == "AGE_REQUIRED"
