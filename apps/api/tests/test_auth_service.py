"""Behavioural tests for `AuthService` — the send/verify orchestrator.

Hits every branch the route layer cares about:
  * send returns the cooldown
  * verify with no pending code → InvalidCodeError
  * verify with wrong code → InvalidCodeError (and code is consumed)
  * verify with right code → JWT + UserPublic with the upsert'd user
  * second verify same phone → reuses the existing user row

Rate-limit policy (PRD §F1 L2) is exercised in `test_auth_rate_limit.py`
at the limiter level and `test_auth_service_rate_limit.py` at the
service level — this file injects a fresh `InMemoryRateLimiter` so the
`/send` and `/verify` paths under test never trip the cap.
"""

from __future__ import annotations

import pytest
from app.services.auth.code_store import InMemoryCodeStore
from app.services.auth.jwt_tokens import decode_token
from app.services.auth.rate_limit import InMemoryRateLimiter
from app.services.auth.service import (
    AuthService,
    InvalidCodeError,
    LoggingDispatcher,
)
from app.services.auth.user_repository import InMemoryUserRepository


def _service() -> tuple[AuthService, InMemoryCodeStore, InMemoryUserRepository]:
    store = InMemoryCodeStore()
    repo = InMemoryUserRepository()
    svc = AuthService(
        code_store=store,
        user_repo=repo,
        dispatcher=LoggingDispatcher(),
        rate_limiter=InMemoryRateLimiter(),
    )
    return svc, store, repo


async def test_send_returns_cooldown_ttl_and_stores_a_code() -> None:
    svc, store, _ = _service()

    resp = await svc.send_code("13800138000")

    assert resp.ttl == 60
    stored = await store.get("13800138000")
    assert stored is not None
    assert len(stored) == 6
    assert stored.isdigit()


async def test_verify_without_send_raises_invalid_code() -> None:
    svc, _, _ = _service()

    with pytest.raises(InvalidCodeError):
        await svc.verify_code("13800138000", "123456")


async def test_verify_with_wrong_code_consumes_the_pending_code() -> None:
    """One bad guess retires the code — no brute-force window.

    The route layer can return 400 either way; this just pins the
    one-shot semantic so retries require a new SMS request."""
    svc, store, _ = _service()
    await svc.send_code("13800138000")
    real_code = await store.get("13800138000")
    assert real_code is not None
    wrong = "000000" if real_code != "000000" else "111111"

    with pytest.raises(InvalidCodeError):
        await svc.verify_code("13800138000", wrong)

    # The wrong guess popped the code; even the correct one no longer works.
    with pytest.raises(InvalidCodeError):
        await svc.verify_code("13800138000", real_code)


async def test_verify_with_right_code_returns_token_and_user() -> None:
    svc, store, repo = _service()
    await svc.send_code("13800138000")
    code = await store.get("13800138000")
    assert code is not None

    resp = await svc.verify_code("13800138000", code)

    assert resp.token  # non-empty JWT
    assert resp.user.id.startswith("u_")
    assert resp.user.persona_type == "in_school"
    assert resp.user.is_minor is False
    # The JWT must decode back to the same user.
    payload = decode_token(resp.token)
    assert payload is not None
    assert payload.user_id == resp.user.id

    # The user lives in the repo for future lookups.
    stored = await repo.get_by_phone("13800138000")
    assert stored is not None
    assert stored.user_id == resp.user.id


async def test_verify_twice_returns_same_user_id() -> None:
    """Re-auth (verify after a new send) must NOT create a duplicate user.

    The second `/send` happens via the store directly because the
    real flow would hit the 60s cooldown — the property under test
    here is repo-upsert idempotency, not the cooldown."""
    from datetime import timedelta

    svc, store, _ = _service()

    await svc.send_code("13800138000")
    code = await store.get("13800138000")
    assert code is not None
    first = await svc.verify_code("13800138000", code)

    # Bypass the 60s send-cooldown — see PRD §F1 L2 — by writing a
    # fresh code straight into the store. We're isolating the user-id
    # idempotency from the rate-limit policy.
    await store.set("13800138000", "654321", ttl=timedelta(minutes=5))
    second = await svc.verify_code("13800138000", "654321")

    assert first.user.id == second.user.id


async def test_verify_consumes_code_so_replay_fails() -> None:
    """A successful verify retires the code — replaying with the same
    code value yields InvalidCodeError next time."""
    svc, store, _ = _service()
    await svc.send_code("13800138000")
    code = await store.get("13800138000")
    assert code is not None
    await svc.verify_code("13800138000", code)

    with pytest.raises(InvalidCodeError):
        await svc.verify_code("13800138000", code)
