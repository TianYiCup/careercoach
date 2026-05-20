"""Fail-closed guard for the SMS dispatcher (H-2).

`LoggingDispatcher` writes the SMS verification code to the
application log in plaintext — fine for dev copy-paste, but an audit
failure (PRD §6.2 / NFR S-05 — codes are credential-grade) AND
functionally broken (no real SMS goes out) in any shared environment.

The guard refuses to wire `LoggingDispatcher` outside
`app_env == "development"`, so a deploy that forgot to inject a real
SMS gateway fails at startup instead of silently shipping. This
mirrors the `jwt_secret` non-dev validator covered by
`test_config_jwt_secret`.
"""

from __future__ import annotations

import pytest
from app.config import get_settings
from app.services.auth import (
    LoggingDispatcher,
    _assert_dispatcher_safe_for_env,
    get_auth_service,
)


class _StubSmsGateway:
    """Stands in for a future real dispatcher (Aliyun / Tencent). Any
    dispatcher that is NOT the logging one must boot fine in prod."""

    name = "stub-gateway"

    async def send(self, *, phone: str, code: str) -> None:  # pragma: no cover
        raise AssertionError("not exercised by these tests")


def test_logging_dispatcher_rejected_in_production() -> None:
    """Prod boot still pinned to the dev dispatcher must fail-fast,
    and the message must name both the env and the dispatcher so the
    deployer knows what to fix."""
    with pytest.raises(RuntimeError) as exc_info:
        _assert_dispatcher_safe_for_env(LoggingDispatcher(), app_env="production")
    msg = str(exc_info.value)
    assert "production" in msg
    assert "LoggingDispatcher" in msg


def test_logging_dispatcher_rejected_in_staging() -> None:
    """Staging is shared infra too — same gate as production."""
    with pytest.raises(RuntimeError):
        _assert_dispatcher_safe_for_env(LoggingDispatcher(), app_env="staging")


def test_logging_dispatcher_allowed_in_development() -> None:
    """Development is the one place plaintext-code logging is OK."""
    _assert_dispatcher_safe_for_env(LoggingDispatcher(), app_env="development")


def test_real_dispatcher_allowed_in_production() -> None:
    """The guard keys on dispatcher TYPE, not just env — once a real
    SMS gateway is injected, a prod boot must succeed."""
    _assert_dispatcher_safe_for_env(_StubSmsGateway(), app_env="production")


@pytest.fixture
def _clear_auth_caches() -> object:
    """`get_settings()` and `get_auth_service()` are both lru_cached.
    Clear before AND after so an `APP_ENV` override in a wiring test
    can't poison either singleton for the rest of the suite."""
    get_settings.cache_clear()
    get_auth_service.cache_clear()
    yield
    get_settings.cache_clear()
    get_auth_service.cache_clear()


def test_get_auth_service_fails_closed_in_production(
    monkeypatch: pytest.MonkeyPatch,
    _clear_auth_caches: object,
) -> None:
    """End-to-end wiring: a production boot with only the logging
    dispatcher available raises rather than returning a service that
    leaks codes to the log."""
    monkeypatch.setenv("APP_ENV", "production")
    # A genuine 32+ byte secret so the jwt_secret validator passes and
    # execution actually reaches the dispatcher guard.
    monkeypatch.setenv("JWT_SECRET", "prod-secret-with-enough-entropy-1234567")
    with pytest.raises(RuntimeError, match="LoggingDispatcher"):
        get_auth_service()


def test_get_auth_service_succeeds_in_development(_clear_auth_caches: object) -> None:
    """Default env is `development` — the factory wires normally."""
    service = get_auth_service()
    assert service is not None
