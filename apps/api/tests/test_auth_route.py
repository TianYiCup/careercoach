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
    InMemoryRateLimiter,
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
        rate_limiter=InMemoryRateLimiter(),
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


# --- 429 rate-limit envelope (PRD §F1 L2) -----------------------------------


async def test_sms_send_within_cooldown_returns_429_with_retry_after(
    client: AsyncClient,
) -> None:
    """The second send within 60 s must return 429 + Retry-After. The
    error code is the dedicated `SMS_SEND_COOLDOWN` so the frontend can
    render a cooldown-specific copy instead of a generic rate-limit
    message."""
    await client.post("/v1/auth/sms/send", json={"phone": "13800138000"})

    resp = await client.post("/v1/auth/sms/send", json={"phone": "13800138000"})

    assert resp.status_code == 429, resp.text
    body = resp.json()
    assert body["code"] == "SMS_SEND_COOLDOWN"
    assert "trace_id" in body
    # Retry-After must carry a positive integer per RFC 6585.
    retry_after = resp.headers.get("Retry-After")
    assert retry_after is not None
    assert int(retry_after) >= 1


async def test_three_wrong_verifies_lock_subsequent_attempts_with_429(
    client: AsyncClient,
    auth_override: InMemoryCodeStore,
) -> None:
    await client.post("/v1/auth/sms/send", json={"phone": "13800138000"})
    real_code = await auth_override.get("13800138000")
    assert real_code is not None

    # Three wrong attempts. Each pops the code (existing one-shot
    # semantic), so we don't bother reseeding — `verify` with no
    # pending code still counts as a failure for the lockout.
    for _ in range(3):
        resp = await client.post(
            "/v1/auth/sms/verify",
            json={"phone": "13800138000", "code": "000000"},
        )
        assert resp.status_code == 400

    # The 4th attempt — even with the right code — must be locked.
    from datetime import timedelta

    await auth_override.set("13800138000", real_code, ttl=timedelta(minutes=5))
    resp = await client.post(
        "/v1/auth/sms/verify",
        json={"phone": "13800138000", "code": real_code},
    )

    assert resp.status_code == 429, resp.text
    body = resp.json()
    assert body["code"] == "SMS_VERIFY_LOCKED"
    retry_after = resp.headers.get("Retry-After")
    assert retry_after is not None
    assert int(retry_after) >= 1


async def test_sms_verify_lock_is_per_phone(
    client: AsyncClient,
    auth_override: InMemoryCodeStore,
) -> None:
    """A locked phone must NOT lock other phones — keeping the cap per
    phone is what makes this usable for shared dev environments."""
    for _ in range(3):
        await client.post(
            "/v1/auth/sms/verify",
            json={"phone": "13800138000", "code": "000000"},
        )

    # Different phone — must still be allowed to send + verify.
    await client.post("/v1/auth/sms/send", json={"phone": "13900139000"})
    code = await auth_override.get("13900139000")
    assert code is not None
    resp = await client.post(
        "/v1/auth/sms/verify",
        json={"phone": "13900139000", "code": code},
    )
    assert resp.status_code == 200, resp.text
