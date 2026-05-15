"""HTTP-layer tests for `POST /v1/review/uploads` — the compliance
attach point for the 复盘师 (review) flow.

Stub semantics: see `test_copilot_route.py` for the rationale. The
review gate ladder is shorter than copilot — minors *are* allowed
into review (text analysis carries no recording red line), so there
is no `MINOR_FORBIDDEN` row.

    no token            → 401 UNAUTHORIZED
    age unset           → 403 AGE_REQUIRED
    age set (any age)   → 501 NOT_IMPLEMENTED

PRD §1.5 only blocks 副驾 + 红线 categories for minors; analytical
review is explicitly OK. The strict moderation tier
(`_apply_minor_strictness`) takes over from there.
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


_SAMPLE_TEXT = "Opponent: free this weekend?\nMe: busy, raincheck."


async def test_no_token_returns_401(client: AsyncClient) -> None:
    resp = await client.post("/v1/review/uploads", json={"text": _SAMPLE_TEXT})

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
        "/v1/review/uploads",
        headers={"Authorization": f"Bearer {token}"},
        json={"text": _SAMPLE_TEXT},
    )

    assert resp.status_code == 403
    assert resp.json()["code"] == "AGE_REQUIRED"


async def test_minor_with_age_set_reaches_stub_501(client: AsyncClient) -> None:
    """Pin: minors must pass the review gate. The route's only block
    is `require_age_set`; `is_minor` does NOT exclude here. If a
    future change accidentally adds `require_adult` to the review
    route, this test red-lights it."""
    token = mint_token(
        user_id="u_minor",
        persona_type="in_school",
        is_minor=True,
        age_set=True,
    )

    resp = await client.post(
        "/v1/review/uploads",
        headers={"Authorization": f"Bearer {token}"},
        json={"text": _SAMPLE_TEXT},
    )

    assert resp.status_code == 501
    assert resp.json()["code"] == "NOT_IMPLEMENTED"


async def test_adult_with_age_set_reaches_stub_501(client: AsyncClient) -> None:
    token = mint_token(
        user_id="u_adult",
        persona_type="intern",
        is_minor=False,
        age_set=True,
    )

    resp = await client.post(
        "/v1/review/uploads",
        headers={"Authorization": f"Bearer {token}"},
        json={"text": _SAMPLE_TEXT},
    )

    assert resp.status_code == 501
    assert resp.json()["code"] == "NOT_IMPLEMENTED"


async def test_text_too_long_returns_422(client: AsyncClient) -> None:
    """PRD §3.3 US-C1 L4 caps text at 5000 chars; longer must 422
    before any handler logic runs."""
    token = mint_token(
        user_id="u_adult",
        persona_type="intern",
        is_minor=False,
        age_set=True,
    )

    resp = await client.post(
        "/v1/review/uploads",
        headers={"Authorization": f"Bearer {token}"},
        json={"text": "x" * 5001},
    )

    assert resp.status_code == 422


async def test_empty_text_returns_422(client: AsyncClient) -> None:
    token = mint_token(
        user_id="u_adult",
        persona_type="intern",
        is_minor=False,
        age_set=True,
    )

    resp = await client.post(
        "/v1/review/uploads",
        headers={"Authorization": f"Bearer {token}"},
        json={"text": ""},
    )

    assert resp.status_code == 422
