"""Config-time guard for `demo_mode` (PR-D1).

The bypass flag turns every /auth/{sms,email}/verify call into a
free JWT mint — exactly what we want for a judge trying the app
during a pitch, exactly what we never want in a shared environment.
The model-validator on `Settings` raises if you try to combine
`demo_mode=True` with `app_env != "development"`.

Mirrors `test_config_jwt_secret.py` for the JWT_SECRET guard.
"""

from __future__ import annotations

import pytest
from app.config import get_settings


@pytest.fixture
def _clear_settings_cache() -> object:
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def test_demo_mode_off_by_default(_clear_settings_cache: object) -> None:
    settings = get_settings()
    assert settings.demo_mode is False


def test_demo_mode_allowed_in_development(
    monkeypatch: pytest.MonkeyPatch,
    _clear_settings_cache: object,
) -> None:
    monkeypatch.setenv("DEMO_MODE", "true")
    monkeypatch.setenv("APP_ENV", "development")
    settings = get_settings()
    assert settings.demo_mode is True


def test_demo_mode_rejected_in_production(
    monkeypatch: pytest.MonkeyPatch,
    _clear_settings_cache: object,
) -> None:
    monkeypatch.setenv("DEMO_MODE", "true")
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("JWT_SECRET", "prod-secret-with-enough-entropy-1234567")
    with pytest.raises(Exception) as exc_info:
        get_settings()
    assert "demo_mode" in str(exc_info.value).lower()
    assert "production" in str(exc_info.value)


def test_demo_mode_rejected_in_staging(
    monkeypatch: pytest.MonkeyPatch,
    _clear_settings_cache: object,
) -> None:
    monkeypatch.setenv("DEMO_MODE", "true")
    monkeypatch.setenv("APP_ENV", "staging")
    monkeypatch.setenv("JWT_SECRET", "staging-secret-with-enough-entropy-12345")
    with pytest.raises(Exception) as exc_info:
        get_settings()
    assert "demo_mode" in str(exc_info.value).lower()
