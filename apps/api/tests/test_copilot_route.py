"""HTTP-layer tests for `POST /v1/copilot/sessions` — the entry
point for the 副驾 (live-coaching) flow.

A-8 pinned the compliance gate ordering while the handler was a 501
stub. A-15 replaces the stub with a real handler that mints a
copilot session row and returns the WebSocket URL the client connects
to (the WS endpoint itself lands in A-17).

What still needs to hold (compliance-critical, do not regress)
--------------------------------------------------------------
    no token            → 401 UNAUTHORIZED
    age unset           → 403 AGE_REQUIRED
    minor (age set)     → 403 MINOR_FORBIDDEN
    adult (age set)     → 200 + copilot_id + ws_url

R-15 in PRD §11.2 (未成年人误用副驾) makes the minor gate non-negotiable.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, datetime
from itertools import count

import pytest
from app.main import app
from app.services.auth import mint_token
from app.services.copilot import (
    CopilotService,
    InMemoryCopilotRepository,
    get_copilot_service,
)
from httpx import ASGITransport, AsyncClient

_WS_BASE_URL = "ws://test.local"


def _deterministic_service() -> tuple[CopilotService, InMemoryCopilotRepository]:
    """Build a service with deterministic id factory + clock so tests
    can assert on stable values without monkeypatching."""
    repo = InMemoryCopilotRepository()
    counter = count(1)
    clock_counter = count(0)
    service = CopilotService(
        repo=repo,
        ws_base_url=_WS_BASE_URL,
        id_factory=lambda: f"cop_test{next(counter):010d}",
        clock=lambda: datetime(2026, 5, 16, 12, next(clock_counter), tzinfo=UTC),
    )
    return service, repo


@pytest.fixture
async def client() -> AsyncIterator[AsyncClient]:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac
    app.dependency_overrides.clear()


def _valid_body() -> dict[str, str]:
    return {"scenario_hint": "interview salary negotiation", "privacy_level": "standard"}


# --------------------------------------------------------------------- #
# Gates (compliance-critical, unchanged from A-8)                        #
# --------------------------------------------------------------------- #


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


async def test_invalid_body_returns_422_before_handler(client: AsyncClient) -> None:
    """Body validation runs before any dependency that takes a body
    arg. An authenticated adult with a bad body must 422, never run
    the handler (which would mint a row for an unusable session)."""
    service, repo = _deterministic_service()
    app.dependency_overrides[get_copilot_service] = lambda: service
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
    # No row was minted — service was never called.
    assert await repo.get("cop_test0000000001") is None


# --------------------------------------------------------------------- #
# Real handler — adult happy path                                        #
# --------------------------------------------------------------------- #


async def test_adult_creates_session_and_gets_copilot_id_plus_ws_url(
    client: AsyncClient,
) -> None:
    """Happy path: adult with age_set → 200 with copilot_id + ws_url.
    The persisted row exists in `pending` state."""
    service, repo = _deterministic_service()
    app.dependency_overrides[get_copilot_service] = lambda: service
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

    assert resp.status_code == 200
    body = resp.json()
    assert body == {
        "copilot_id": "cop_test0000000001",
        "ws_url": f"{_WS_BASE_URL}/v1/copilot/sessions/cop_test0000000001/stream",
    }

    # Persistence sanity — row exists in `pending` state, attributed
    # to the JWT-derived user_id (NOT self-reported).
    record = await repo.get("cop_test0000000001")
    assert record is not None
    assert record.status == "pending"
    assert record.user_id == "u_adult"
    assert record.scenario_hint == "interview salary negotiation"
    assert record.privacy_level == "standard"
    assert record.connected_at is None
    assert record.ended_at is None


async def test_high_privacy_level_persists_unchanged(client: AsyncClient) -> None:
    """`privacy_level=high` flows from request → record. The future
    ASR adapter (A-16) routes on this value; A-15 just stores it."""
    service, repo = _deterministic_service()
    app.dependency_overrides[get_copilot_service] = lambda: service
    token = mint_token(
        user_id="u_adult",
        persona_type="intern",
        is_minor=False,
        age_set=True,
    )

    resp = await client.post(
        "/v1/copilot/sessions",
        headers={"Authorization": f"Bearer {token}"},
        json={"scenario_hint": "interview salary negotiation", "privacy_level": "high"},
    )

    assert resp.status_code == 200
    record = await repo.get(resp.json()["copilot_id"])
    assert record is not None
    assert record.privacy_level == "high"


async def test_ws_url_is_built_from_service_base_url(client: AsyncClient) -> None:
    """The ws_url shape is `{base}/v1/copilot/sessions/{id}/stream` —
    tests pin this so A-17's WS endpoint can mount at the matching
    path with confidence."""
    service, _ = _deterministic_service()
    app.dependency_overrides[get_copilot_service] = lambda: service
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

    ws_url = resp.json()["ws_url"]
    assert ws_url.startswith(_WS_BASE_URL)
    assert ws_url.endswith("/stream")
    assert "/v1/copilot/sessions/cop_test" in ws_url


async def test_each_post_mints_a_unique_copilot_id(client: AsyncClient) -> None:
    """Two POSTs from the same user must mint distinct rows — there
    is no de-duplication / replay of in-flight sessions in v0."""
    service, repo = _deterministic_service()
    app.dependency_overrides[get_copilot_service] = lambda: service
    token = mint_token(
        user_id="u_adult",
        persona_type="intern",
        is_minor=False,
        age_set=True,
    )
    headers = {"Authorization": f"Bearer {token}"}

    first = await client.post("/v1/copilot/sessions", headers=headers, json=_valid_body())
    second = await client.post("/v1/copilot/sessions", headers=headers, json=_valid_body())

    assert first.status_code == 200
    assert second.status_code == 200
    first_id = first.json()["copilot_id"]
    second_id = second.json()["copilot_id"]
    assert first_id != second_id
    # Both rows are persisted.
    assert await repo.get(first_id) is not None
    assert await repo.get(second_id) is not None
