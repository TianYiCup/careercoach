"""HTTP-layer tests for `/v1/auth/email/send` + `/v1/auth/email/verify`.

Overrides the default `EmailAuthService` with one wired to fresh
in-memory stores per test so codes don't leak across the boundary.
Mirrors `test_auth_route.py` for the SMS path.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterator

import pytest
from app.main import app
from app.services.auth import (
    EmailAuthService,
    InMemoryCodeStore,
    InMemoryRateLimiter,
    InMemoryUserRepository,
    LoggingEmailDispatcher,
    get_email_auth_service,
)
from httpx import ASGITransport, AsyncClient


@pytest.fixture
def email_auth_override() -> Iterator[InMemoryCodeStore]:
    store = InMemoryCodeStore()
    service = EmailAuthService(
        code_store=store,
        user_repo=InMemoryUserRepository(),
        dispatcher=LoggingEmailDispatcher(),
        rate_limiter=InMemoryRateLimiter(),
    )
    app.dependency_overrides[get_email_auth_service] = lambda: service
    try:
        yield store
    finally:
        app.dependency_overrides.pop(get_email_auth_service, None)


@pytest.fixture
async def client(email_auth_override: InMemoryCodeStore) -> AsyncIterator[AsyncClient]:
    _ = email_auth_override
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


async def test_email_send_returns_200_with_cooldown_ttl(client: AsyncClient) -> None:
    resp = await client.post("/v1/auth/email/send", json={"email": "alex@example.com"})
    assert resp.status_code == 200, resp.text
    assert resp.json() == {"ttl": 60}


@pytest.mark.parametrize(
    "bad_email",
    ["not-an-email", "alex@", "@example.com", "alex example@example.com", "", "a" * 300],
)
async def test_email_send_rejects_malformed(client: AsyncClient, bad_email: str) -> None:
    resp = await client.post("/v1/auth/email/send", json={"email": bad_email})
    assert resp.status_code == 422
    assert resp.json()["code"] == "VALIDATION_ERROR"


async def test_email_verify_with_pending_code_returns_jwt_and_user(
    client: AsyncClient,
    email_auth_override: InMemoryCodeStore,
) -> None:
    await client.post("/v1/auth/email/send", json={"email": "alex@example.com"})
    code = await email_auth_override.get("alex@example.com")
    assert code is not None

    resp = await client.post(
        "/v1/auth/email/verify",
        json={"email": "alex@example.com", "code": code},
    )

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["token"]
    assert body["user"]["id"].startswith("u_")
    assert body["user"]["nickname"]
    assert body["user"]["persona_type"] == "in_school"
    assert body["user"]["is_minor"] is False


async def test_email_verify_with_wrong_code_returns_400(
    client: AsyncClient,
    email_auth_override: InMemoryCodeStore,
) -> None:
    _ = email_auth_override
    await client.post("/v1/auth/email/send", json={"email": "alex@example.com"})

    resp = await client.post(
        "/v1/auth/email/verify",
        json={"email": "alex@example.com", "code": "999999"},
    )
    assert resp.status_code == 400
    assert resp.json()["code"] == "INVALID_CODE"


async def test_email_verify_with_no_pending_code_returns_400(client: AsyncClient) -> None:
    resp = await client.post(
        "/v1/auth/email/verify",
        json={"email": "alex@example.com", "code": "123456"},
    )
    assert resp.status_code == 400
    assert resp.json()["code"] == "INVALID_CODE"


async def test_email_send_second_call_returns_429_with_retry_after(client: AsyncClient) -> None:
    await client.post("/v1/auth/email/send", json={"email": "alex@example.com"})
    resp = await client.post("/v1/auth/email/send", json={"email": "alex@example.com"})
    assert resp.status_code == 429
    assert resp.json()["code"] == "EMAIL_SEND_COOLDOWN"
    assert resp.headers.get("Retry-After")
    # Body carries Chinese-friendly copy with the seconds-left.
    assert "秒" in resp.json()["message"]


async def test_email_verify_three_wrong_codes_locks_with_retry_after(
    client: AsyncClient,
) -> None:
    for _ in range(3):
        await client.post("/v1/auth/email/send", json={"email": "alex@example.com"})
        wrong = await client.post(
            "/v1/auth/email/verify",
            json={"email": "alex@example.com", "code": "999999"},
        )
        assert wrong.status_code == 400
    # Fourth attempt is locked even with the right shape.
    locked = await client.post(
        "/v1/auth/email/verify",
        json={"email": "alex@example.com", "code": "123456"},
    )
    assert locked.status_code == 429
    assert locked.json()["code"] == "EMAIL_VERIFY_LOCKED"
    assert locked.headers.get("Retry-After")


async def test_email_verify_rejects_short_code(client: AsyncClient) -> None:
    resp = await client.post(
        "/v1/auth/email/verify",
        json={"email": "alex@example.com", "code": "12345"},
    )
    assert resp.status_code == 422


async def test_openapi_has_both_send_and_verify(client: AsyncClient) -> None:
    """OpenAPI surfacing — guards against accidentally unregistering."""
    resp = await client.get("/openapi.json")
    assert resp.status_code == 200
    paths = resp.json()["paths"]
    assert "/v1/auth/email/send" in paths
    assert "/v1/auth/email/verify" in paths
