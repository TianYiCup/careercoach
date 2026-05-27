"""Demo-mode bypass (PR-D1).

When `demo_mode=True`:
* SMS + email verify accept ANY 6-digit code (schema-validated)
* No code-store check, no rate-limit check, no failure recording
* The user is upserted exactly as in the normal flow

The config-time validator refuses to start the app with `demo_mode=True`
outside `app_env=development` — covered in `test_config_demo_mode.py`.
This file only exercises the service behaviour under the flag.
"""

from __future__ import annotations

import pytest
from app.services.auth import (
    AuthService,
    EmailAuthService,
    InMemoryCodeStore,
    InMemoryRateLimiter,
    InMemoryUserRepository,
    LoggingDispatcher,
    LoggingEmailDispatcher,
)
from structlog.testing import capture_logs


def _sms_service(*, demo_mode: bool) -> tuple[AuthService, InMemoryCodeStore]:
    store = InMemoryCodeStore()
    return (
        AuthService(
            code_store=store,
            user_repo=InMemoryUserRepository(),
            dispatcher=LoggingDispatcher(),
            rate_limiter=InMemoryRateLimiter(),
            demo_mode=demo_mode,
        ),
        store,
    )


def _email_service(*, demo_mode: bool) -> tuple[EmailAuthService, InMemoryCodeStore]:
    store = InMemoryCodeStore()
    return (
        EmailAuthService(
            code_store=store,
            user_repo=InMemoryUserRepository(),
            dispatcher=LoggingEmailDispatcher(),
            rate_limiter=InMemoryRateLimiter(),
            demo_mode=demo_mode,
        ),
        store,
    )


class TestSmsDemoMode:
    @pytest.mark.asyncio
    async def test_verify_succeeds_with_any_code_when_demo_on(self) -> None:
        service, _ = _sms_service(demo_mode=True)
        resp = await service.verify_code("13800138000", "000000")
        assert resp.token
        assert resp.user.id.startswith("u_")
        assert resp.user.persona_type == "in_school"

    @pytest.mark.asyncio
    async def test_verify_without_pending_code_still_works_in_demo(self) -> None:
        """Whole point — no /send call, judge types `000000`, gets in."""
        service, store = _sms_service(demo_mode=True)
        # Confirm nothing was stored — we still mint a token.
        assert await store.pop("13800138000") is None
        resp = await service.verify_code("13800138000", "999999")
        assert resp.token

    @pytest.mark.asyncio
    async def test_verify_in_demo_skips_lock_check(self) -> None:
        """Three failed attempts in demo mode shouldn't lock the user
        because demo never records failures — and even if it did, the
        bypass branch returns before the rate-limit check runs."""
        service, _ = _sms_service(demo_mode=True)
        for _ in range(5):
            resp = await service.verify_code("13800138000", "111111")
            assert resp.token

    @pytest.mark.asyncio
    async def test_returning_user_keeps_same_id_in_demo(self) -> None:
        service, _ = _sms_service(demo_mode=True)
        first = await service.verify_code("13800138000", "000000")
        second = await service.verify_code("13800138000", "999999")
        assert first.user.id == second.user.id

    @pytest.mark.asyncio
    async def test_verify_logs_demo_event_with_masked_phone(self) -> None:
        service, _ = _sms_service(demo_mode=True)
        with capture_logs() as logs:
            await service.verify_code("13800138000", "000000")
        events = [e.get("event") for e in logs]
        assert "sms_demo_mode_login" in events
        # Raw phone must NOT appear; masked one should.
        flat = " ".join(f"{k}={v}" for entry in logs for k, v in entry.items())
        assert "138****8000" in flat
        assert "13800138000" not in flat

    @pytest.mark.asyncio
    async def test_demo_off_still_enforces_code(self) -> None:
        """Sanity — flipping the flag off must keep the strict path."""
        from app.services.auth import InvalidCodeError

        service, _ = _sms_service(demo_mode=False)
        with pytest.raises(InvalidCodeError):
            await service.verify_code("13800138000", "000000")


class TestEmailDemoMode:
    @pytest.mark.asyncio
    async def test_verify_succeeds_with_any_code_when_demo_on(self) -> None:
        service, _ = _email_service(demo_mode=True)
        resp = await service.verify_code("alex@example.com", "000000")
        assert resp.token
        assert resp.user.id.startswith("u_")

    @pytest.mark.asyncio
    async def test_verify_logs_demo_event_with_masked_email(self) -> None:
        service, _ = _email_service(demo_mode=True)
        with capture_logs() as logs:
            await service.verify_code("alex@example.com", "000000")
        events = [e.get("event") for e in logs]
        assert "email_demo_mode_login" in events
        flat = " ".join(f"{k}={v}" for entry in logs for k, v in entry.items())
        assert "a***@example.com" in flat
        assert "alex@example.com" not in flat

    @pytest.mark.asyncio
    async def test_demo_off_still_enforces_code(self) -> None:
        from app.services.auth import InvalidEmailCodeError

        service, _ = _email_service(demo_mode=False)
        with pytest.raises(InvalidEmailCodeError):
            await service.verify_code("alex@example.com", "000000")
