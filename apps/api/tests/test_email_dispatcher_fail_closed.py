"""Fail-closed guard for the email dispatcher (PR-A2).

`LoggingEmailDispatcher` writes the verification code to the app log
in plaintext. That is fine for dev copy-paste, but in staging or
production it is an audit failure (PRD §6.2 / NFR S-05) AND
functionally broken (no real email goes out).

Mirrors `test_auth_dispatcher_fail_closed.py` for the SMS path.
"""

from __future__ import annotations

import pytest
from app.config import get_settings
from app.services.auth import (
    LoggingEmailDispatcher,
    _assert_email_dispatcher_safe_for_env,
    get_email_auth_service,
)


class _StubEmailGateway:
    """Stands in for a real `EmailDispatcher` (Resend / future Aliyun
    Direct Mail). Any non-logging dispatcher must boot fine in prod."""

    name = "stub-gateway"

    async def send(self, *, email: str, code: str) -> None:  # pragma: no cover
        raise AssertionError("not exercised by these tests")


def test_logging_email_dispatcher_rejected_in_production() -> None:
    with pytest.raises(RuntimeError) as exc_info:
        _assert_email_dispatcher_safe_for_env(LoggingEmailDispatcher(), app_env="production")
    msg = str(exc_info.value)
    assert "production" in msg
    assert "LoggingEmailDispatcher" in msg


def test_logging_email_dispatcher_rejected_in_staging() -> None:
    with pytest.raises(RuntimeError):
        _assert_email_dispatcher_safe_for_env(LoggingEmailDispatcher(), app_env="staging")


def test_logging_email_dispatcher_allowed_in_development() -> None:
    _assert_email_dispatcher_safe_for_env(LoggingEmailDispatcher(), app_env="development")


def test_real_email_dispatcher_allowed_in_production() -> None:
    _assert_email_dispatcher_safe_for_env(_StubEmailGateway(), app_env="production")


@pytest.fixture
def _clear_caches() -> object:
    get_settings.cache_clear()
    get_email_auth_service.cache_clear()
    yield
    get_settings.cache_clear()
    get_email_auth_service.cache_clear()


def test_get_email_auth_service_fails_closed_in_production(
    monkeypatch: pytest.MonkeyPatch,
    _clear_caches: object,
) -> None:
    """Production boot with the default `logging` backend raises."""
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("JWT_SECRET", "prod-secret-with-enough-entropy-1234567")
    # Leave AUTH_EMAIL_DISPATCHER_BACKEND unset → defaults to `logging`.
    with pytest.raises(RuntimeError, match="LoggingEmailDispatcher"):
        get_email_auth_service()


def test_get_email_auth_service_succeeds_in_development(_clear_caches: object) -> None:
    """Default env is `development` — the factory wires normally."""
    service = get_email_auth_service()
    assert service is not None


def test_get_email_auth_service_smtp_requires_smtp_env(
    monkeypatch: pytest.MonkeyPatch,
    _clear_caches: object,
) -> None:
    """A prod deploy that flipped to `smtp` but forgot the SMTP_*
    envs fails fast via `SmtpConfigError`, not at first /send call."""
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("JWT_SECRET", "prod-secret-with-enough-entropy-1234567")
    monkeypatch.setenv("AUTH_EMAIL_DISPATCHER_BACKEND", "smtp")
    # Leave SMTP_HOST / SMTP_USERNAME / etc. unset.
    with pytest.raises(Exception) as exc_info:
        get_email_auth_service()
    # SmtpConfigError extends ValueError; surfaces as a misconfig.
    assert "SMTP" in str(exc_info.value) or "misconfigured" in str(exc_info.value)


def test_get_email_auth_service_smtp_path_constructs_when_env_complete(
    monkeypatch: pytest.MonkeyPatch,
    _clear_caches: object,
) -> None:
    """Happy prod path — backend=smtp + all envs set → service wires."""
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("JWT_SECRET", "prod-secret-with-enough-entropy-1234567")
    monkeypatch.setenv("AUTH_EMAIL_DISPATCHER_BACKEND", "smtp")
    monkeypatch.setenv("SMTP_HOST", "smtp.resend.com")
    monkeypatch.setenv("SMTP_PORT", "587")
    monkeypatch.setenv("SMTP_USERNAME", "resend")
    monkeypatch.setenv("SMTP_PASSWORD", "re_test_apikey")
    monkeypatch.setenv("SMTP_FROM", "noreply@careercoach.app")
    service = get_email_auth_service()
    assert service is not None
