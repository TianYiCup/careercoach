"""HTTP-layer tests for `/v1/auth/sms/send` + `/v1/auth/sms/verify`.

Overrides the default `AuthService` with one wired to fresh in-memory
stores per test so codes don't leak across the boundary.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterator

import pytest
from app.main import app
from app.services.auth import (
    AuthService,
    InMemoryCodeStore,
    InMemoryUserRepository,
    LoggingDispatcher,
    get_auth_service,
)
from httpx import ASGITransport, AsyncClient


@pytest.fixture
def auth_override() -> Iterator[InMemoryCodeStore]:
    store = InMemoryCodeStore()
    service = AuthService(
        code_store=store,
        user_repo=InMemoryUserRepository(),
        dispatcher=LoggingDispatcher(),
    )
    app.dependency_overrides[get_auth_service] = lambda: service
    try:
        yield store
    finally:
        app.dependency_overrides.pop(get_auth_service, None)


@pytest.fixture
async def client(auth_override: InMemoryCodeStore) -> AsyncIterator[AsyncClient]:
    _ = auth_override
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


async def test_sms_send_returns_200_with_cooldown_ttl(client: AsyncClient) -> None:
    resp = await client.post("/v1/auth/sms/send", json={"phone": "13800138000"})

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body == {"ttl": 60}


async def test_sms_send_rejects_invalid_phone(client: AsyncClient) -> None:
    resp = await client.post("/v1/auth/sms/send", json={"phone": "12345"})
    assert resp.status_code == 422
    assert resp.json()["code"] == "VALIDATION_ERROR"


async def test_sms_verify_with_pending_code_returns_jwt_and_user(
    client: AsyncClient,
    auth_override: InMemoryCodeStore,
) -> None:
    await client.post("/v1/auth/sms/send", json={"phone": "13800138000"})
    code = await auth_override.get("13800138000")
    assert code is not None

    resp = await client.post(
        "/v1/auth/sms/verify",
        json={"phone": "13800138000", "code": code},
    )

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["token"]  # non-empty
    assert body["user"]["id"].startswith("u_")
    assert body["user"]["nickname"]
    assert body["user"]["persona_type"] == "in_school"
    assert body["user"]["is_minor"] is False


async def test_sms_verify_with_wrong_code_returns_400(
    client: AsyncClient,
    auth_override: InMemoryCodeStore,
) -> None:
    await client.post("/v1/auth/sms/send", json={"phone": "13800138000"})
    code = await auth_override.get("13800138000")
    assert code is not None
    wrong = "000000" if code != "000000" else "999999"

    resp = await client.post(
        "/v1/auth/sms/verify",
        json={"phone": "13800138000", "code": wrong},
    )

    assert resp.status_code == 400
    body = resp.json()
    assert body["code"] == "INVALID_CODE"


async def test_sms_verify_without_send_returns_400(client: AsyncClient) -> None:
    resp = await client.post(
        "/v1/auth/sms/verify",
        json={"phone": "13800138000", "code": "123456"},
    )

    assert resp.status_code == 400
    body = resp.json()
    assert body["code"] == "INVALID_CODE"


async def test_sms_verify_rejects_non_6_digit_code(client: AsyncClient) -> None:
    """Pattern validation on the schema rejects before the service runs."""
    resp = await client.post(
        "/v1/auth/sms/verify",
        json={"phone": "13800138000", "code": "12345"},  # 5 digits
    )
    assert resp.status_code == 422
