"""HTTP-layer tests for `POST /v1/users/me/birth-year`.

Pins:
  * happy path — minor JWT gets re-minted with `is_minor=True`
  * 401 when no bearer token
  * 422 on out-of-range / future birth year
  * 404 when the JWT subject doesn't exist in the repo
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
    mint_token,
)
from app.services.auth.jwt_tokens import decode_token
from httpx import ASGITransport, AsyncClient


@pytest.fixture
def auth_override() -> Iterator[InMemoryUserRepository]:
    repo = InMemoryUserRepository()
    service = AuthService(
        code_store=InMemoryCodeStore(),
        user_repo=repo,
        dispatcher=LoggingDispatcher(),
        rate_limiter=InMemoryRateLimiter(),
    )
    app.dependency_overrides[get_auth_service] = lambda: service
    try:
        yield repo
    finally:
        app.dependency_overrides.pop(get_auth_service, None)


@pytest.fixture
async def client(auth_override: InMemoryUserRepository) -> AsyncIterator[AsyncClient]:
    _ = auth_override
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


async def _bootstrap_user(repo: InMemoryUserRepository) -> str:
    record = await repo.create(
        phone="13800138000",
        nickname="K 学员 8000",
        persona_type="in_school",
        is_minor=False,
    )
    return mint_token(
        user_id=record.user_id,
        persona_type=record.persona_type,
        is_minor=record.is_minor,
    )


async def test_set_minor_birth_year_re_mints_jwt_with_flag(
    client: AsyncClient,
    auth_override: InMemoryUserRepository,
) -> None:
    token = await _bootstrap_user(auth_override)

    resp = await client.post(
        "/v1/users/me/birth-year",
        headers={"Authorization": f"Bearer {token}"},
        json={"birth_year": 2012},
    )

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["user"]["is_minor"] is True
    # The new JWT must carry the flipped flag — otherwise the client
    # gets a "you're a minor" UserPublic but their next request still
    # gets the adult tier from the old token.
    payload = decode_token(body["token"])
    assert payload is not None
    assert payload.is_minor is True


async def test_set_adult_birth_year_keeps_minor_false(
    client: AsyncClient,
    auth_override: InMemoryUserRepository,
) -> None:
    token = await _bootstrap_user(auth_override)

    resp = await client.post(
        "/v1/users/me/birth-year",
        headers={"Authorization": f"Bearer {token}"},
        json={"birth_year": 2000},
    )

    assert resp.status_code == 200
    assert resp.json()["user"]["is_minor"] is False


async def test_missing_bearer_token_returns_401(client: AsyncClient) -> None:
    resp = await client.post(
        "/v1/users/me/birth-year",
        json={"birth_year": 2003},
    )

    assert resp.status_code == 401
    assert resp.json()["code"] == "UNAUTHORIZED"


async def test_future_birth_year_is_treated_as_minor_fail_closed(
    client: AsyncClient,
    auth_override: InMemoryUserRepository,
) -> None:
    """Security-meaningful property: a typo (or a hostile user setting
    `birth_year=2099` to bypass the gate) MUST NOT result in
    `is_minor=False`. `compute_is_minor` returns True for negative
    ages, so the typoed caller lands on the strict tier — exactly the
    fail-closed behavior we want for a minor gate."""
    token = await _bootstrap_user(auth_override)

    resp = await client.post(
        "/v1/users/me/birth-year",
        headers={"Authorization": f"Bearer {token}"},
        json={"birth_year": 2099},  # well in the future
    )

    assert resp.status_code == 200, resp.text
    assert resp.json()["user"]["is_minor"] is True


async def test_birth_year_grossly_out_of_range_returns_422(
    client: AsyncClient,
    auth_override: InMemoryUserRepository,
) -> None:
    token = await _bootstrap_user(auth_override)

    resp = await client.post(
        "/v1/users/me/birth-year",
        headers={"Authorization": f"Bearer {token}"},
        json={"birth_year": 1899},
    )

    assert resp.status_code == 422


async def test_unknown_jwt_subject_returns_404(
    client: AsyncClient,
) -> None:
    """JWT verifies but the subject doesn't exist in the repo — e.g.
    DB wipe with old token still in client's localStorage. Must be
    404, never 500."""
    stale_token = mint_token(
        user_id="u_ghost-account",
        persona_type="in_school",
        is_minor=False,
    )

    resp = await client.post(
        "/v1/users/me/birth-year",
        headers={"Authorization": f"Bearer {stale_token}"},
        json={"birth_year": 2003},
    )

    assert resp.status_code == 404
    assert resp.json()["code"] == "NOT_FOUND"
