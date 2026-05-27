"""Unit tests for `EmailAuthService` (PR-A2).

Covers the happy path + the four typed failure modes (rate-limited
send, rate-limited verify, expired/missing code, wrong code) and the
user-upsert path. No HTTP layer here — that lives in
`test_email_auth_routes.py`.
"""

from __future__ import annotations

import pytest
from app.services.auth.code_store import InMemoryCodeStore
from app.services.auth.email_dispatcher import LoggingEmailDispatcher
from app.services.auth.email_service import (
    EmailAuthService,
    InvalidEmailCodeError,
)
from app.services.auth.rate_limit import InMemoryRateLimiter, RateLimited
from app.services.auth.user_repository import InMemoryUserRepository
from structlog.testing import capture_logs


def _build_service() -> tuple[EmailAuthService, InMemoryUserRepository, InMemoryCodeStore]:
    code_store = InMemoryCodeStore()
    user_repo = InMemoryUserRepository()
    service = EmailAuthService(
        code_store=code_store,
        user_repo=user_repo,
        dispatcher=LoggingEmailDispatcher(),
        rate_limiter=InMemoryRateLimiter(),
    )
    return service, user_repo, code_store


class TestSendCode:
    @pytest.mark.asyncio
    async def test_returns_ttl_envelope(self) -> None:
        service, _, _ = _build_service()
        resp = await service.send_code("alex@example.com")
        assert resp.ttl == 60

    @pytest.mark.asyncio
    async def test_stores_code_in_codestore(self) -> None:
        service, _, code_store = _build_service()
        await service.send_code("alex@example.com")
        stored = await code_store.pop("alex@example.com")
        assert stored is not None
        assert stored.isdigit() and len(stored) == 6

    @pytest.mark.asyncio
    async def test_lowercases_address_before_storing(self) -> None:
        service, _, code_store = _build_service()
        await service.send_code("Alex@Example.COM")
        # Stored under normalised key — verify_code with the original
        # mixed-case input must still find it (next test).
        stored_lower = await code_store.pop("alex@example.com")
        assert stored_lower is not None
        # And the mixed-case key has nothing.
        stored_orig = await code_store.pop("Alex@Example.COM")
        assert stored_orig is None

    @pytest.mark.asyncio
    async def test_second_send_within_cooldown_raises(self) -> None:
        service, _, _ = _build_service()
        await service.send_code("alex@example.com")
        with pytest.raises(RateLimited) as ctx:
            await service.send_code("alex@example.com")
        assert ctx.value.kind == "send_cooldown"
        assert ctx.value.retry_after_seconds > 0


class TestVerifyCode:
    @pytest.mark.asyncio
    async def test_round_trip_mints_token_and_creates_user(self) -> None:
        service, user_repo, code_store = _build_service()
        await service.send_code("alex@example.com")
        code = await code_store.pop("alex@example.com")
        assert code is not None
        # Round-trip: put it back so verify can pop it. (The send-time
        # rate limit will already have set the send cooldown — that's
        # an artefact of the test, not the real flow.)
        await code_store.set(
            "alex@example.com",
            code,
            ttl=__import__("datetime").timedelta(minutes=5),
        )

        resp = await service.verify_code("alex@example.com", code)
        assert resp.token  # JWT was minted
        assert resp.user.id.startswith("u_")
        assert resp.user.persona_type == "in_school"
        assert resp.user.is_minor is False

        # User was persisted under the email key.
        stored = await user_repo.get_by_email("alex@example.com")
        assert stored is not None
        assert stored.user_id == resp.user.id
        assert stored.email == "alex@example.com"
        assert stored.phone is None

    @pytest.mark.asyncio
    async def test_verify_with_mixed_case_email_lookups_lowercase(self) -> None:
        service, _, code_store = _build_service()
        await service.send_code("alex@example.com")
        code = await code_store.pop("alex@example.com")
        assert code is not None
        await code_store.set(
            "alex@example.com",
            code,
            ttl=__import__("datetime").timedelta(minutes=5),
        )
        # Verifier types the address in mixed case — should still hit.
        resp = await service.verify_code("Alex@Example.COM", code)
        assert resp.token

    @pytest.mark.asyncio
    async def test_returning_user_keeps_same_id(self) -> None:
        service, user_repo, code_store = _build_service()

        async def _round_trip() -> str:
            await code_store.set(
                "alex@example.com",
                "123456",
                ttl=__import__("datetime").timedelta(minutes=5),
            )
            resp = await service.verify_code("alex@example.com", "123456")
            return resp.user.id

        first_id = await _round_trip()
        second_id = await _round_trip()
        assert first_id == second_id
        # Single record in the repo.
        record = await user_repo.get_by_email("alex@example.com")
        assert record is not None
        assert record.user_id == first_id

    @pytest.mark.asyncio
    async def test_wrong_code_raises_invalid(self) -> None:
        service, _, code_store = _build_service()
        await code_store.set(
            "alex@example.com",
            "123456",
            ttl=__import__("datetime").timedelta(minutes=5),
        )
        with pytest.raises(InvalidEmailCodeError, match="does not match"):
            await service.verify_code("alex@example.com", "000000")

    @pytest.mark.asyncio
    async def test_no_pending_code_raises_invalid(self) -> None:
        service, _, _ = _build_service()
        with pytest.raises(InvalidEmailCodeError, match="expired or never requested"):
            await service.verify_code("alex@example.com", "123456")

    @pytest.mark.asyncio
    async def test_three_failures_lock(self) -> None:
        service, _, code_store = _build_service()
        for _ in range(3):
            await code_store.set(
                "alex@example.com",
                "111111",
                ttl=__import__("datetime").timedelta(minutes=5),
            )
            with pytest.raises(InvalidEmailCodeError):
                await service.verify_code("alex@example.com", "999999")
        # Fourth call hits the lock — even if a fresh code is set.
        await code_store.set(
            "alex@example.com",
            "111111",
            ttl=__import__("datetime").timedelta(minutes=5),
        )
        with pytest.raises(RateLimited) as ctx:
            await service.verify_code("alex@example.com", "111111")
        assert ctx.value.kind == "verify_locked"


class TestLogging:
    @pytest.mark.asyncio
    async def test_send_path_masks_email_in_log(self) -> None:
        service, _, _ = _build_service()
        with capture_logs() as logs:
            await service.send_code("alex@example.com")
        flat = " ".join(f"{k}={v}" for entry in logs for k, v in entry.items())
        assert "a***@example.com" in flat
        assert "alex@example.com" not in flat

    @pytest.mark.asyncio
    async def test_verify_path_masks_email_in_log(self) -> None:
        service, _, code_store = _build_service()
        await code_store.set(
            "alex@example.com",
            "123456",
            ttl=__import__("datetime").timedelta(minutes=5),
        )
        with capture_logs() as logs:
            await service.verify_code("alex@example.com", "123456")
        flat = " ".join(f"{k}={v}" for entry in logs for k, v in entry.items())
        assert "a***@example.com" in flat
        assert "alex@example.com" not in flat
