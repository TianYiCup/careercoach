"""HTTP-layer tests for `POST /v1/copilot/sessions` — the compliance
attach point for the 副驾 (live-coaching) flow.

The route is a *stub*: no business logic exists yet. What this file
pins is the **gate ordering** — the order of failure must match the
documented contract so B can wire one error handler per code without
worrying about case shadowing:

    no token            → 401 UNAUTHORIZED
    age unset           → 403 AGE_REQUIRED
    minor (age set)     → 403 MINOR_FORBIDDEN
    adult (age set)     → 501 NOT_IMPLEMENTED

When the real handler ships, only the last row changes — the other
three rows are *load-bearing for PRD §1.5 / §3.0.5 C / R-15* and must
keep passing.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest
from app.main import app
from app.services.auth import mint_token
from httpx import ASGITransport, AsyncClient


@pytest.fixture
async def client() -> AsyncIterator[AsyncClient]:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


def _valid_body() -> dict[str, str]:
    return {"scenario_hint": "面试谈薪", "privacy_level": "standard"}


async def test_no_token_returns_401(client: AsyncClient) -> None:
    resp = await client.post("/v1/copilot/sessions", json=_valid_body())

    assert resp.status_code == 401
    assert resp.json()["code"] == "UNAUTHORIZED"


async def test_age_unset_returns_403_age_required(client: AsyncClient) -> None:
    token = mint_token(
        user_id="u_no_age",
        persona_type="intern",
        is_minor=False,
        age_set=False,
    )

    resp = await client.post(
        "/v1/copilot/sessions",
        headers={"Authorization": f"Bearer {token}"},
        json=_valid_body(),
    )

    assert resp.status_code == 403
    assert resp.json()["code"] == "AGE_REQUIRED"


async def test_minor_returns_403_minor_forbidden(client: AsyncClient) -> None:
    """The compliance-critical case: a known minor must never reach
    the copilot business logic. R-15 in PRD §11.2."""
    token = mint_token(
        user_id="u_minor",
        persona_type="in_school",
        is_minor=True,
        age_set=True,
    )

    resp = await client.post(
        "/v1/copilot/sessions",
        headers={"Authorization": f"Bearer {token}"},
        json=_valid_body(),
    )

    assert resp.status_code == 403
    assert resp.json()["code"] == "MINOR_FORBIDDEN"


async def test_adult_with_age_set_reaches_stub_501(client: AsyncClient) -> None:
    """Positive control — once gates pass, the handler runs and the
    stub responds with 501 NOT_IMPLEMENTED. When the real handler
    ships, swap this to assert 200 + a copilot_id."""
    token = mint_token(
        user_id="u_adult",
        persona_type="intern",
        is_minor=False,
        age_set=True,
    )

    resp = await client.post(
        "/v1/copilot/sessions",
        headers={"Authorization": f"Bearer {token}"},
        json=_valid_body(),
    )

    assert resp.status_code == 501
    assert resp.json()["code"] == "NOT_IMPLEMENTED"


async def test_invalid_body_returns_422_before_gates(client: AsyncClient) -> None:
    """Body validation runs before any dependency that takes a body
    arg, but FastAPI evaluates Depends() *first* for parameters that
    aren't tied to the body. Either way, an authenticated adult with
    a bad body must end up at 422, never 501 — protects against the
    handler silently accepting malformed input once the real
    implementation lands."""
    token = mint_token(
        user_id="u_adult",
        persona_type="intern",
        is_minor=False,
        age_set=True,
    )

    resp = await client.post(
        "/v1/copilot/sessions",
        headers={"Authorization": f"Bearer {token}"},
        json={"scenario_hint": ""},  # min_length=1 violated
    )

    assert resp.status_code == 422
