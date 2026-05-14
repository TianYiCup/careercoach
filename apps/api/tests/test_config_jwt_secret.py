"""Settings validation tests for `jwt_secret`.

The validator catches three classes of mistake at process startup:
  1. Production / staging deploy that forgot to set JWT_SECRET → would
     otherwise sign tokens with the public dev default.
  2. Any environment with a too-short secret.
  3. Dev runs with the canonical default → log warning but allow.

These run against `Settings(...)` directly (not the `get_settings()`
singleton) so each test gets a fresh validation pass without
poisoning the lru_cache.
"""

from __future__ import annotations

import pytest
from app.config import DEV_JWT_SECRET, Settings
from pydantic import SecretStr, ValidationError


def test_default_settings_pass_in_development() -> None:
    """The dev default IS the dev secret; the validator allows it
    when `app_env == "development"` but logs a warning."""
    settings = Settings()
    assert settings.app_env == "development"
    assert settings.jwt_secret.get_secret_value() == DEV_JWT_SECRET


def test_production_with_dev_secret_raises() -> None:
    """Prod deploy that forgot to set JWT_SECRET — must fail-fast on
    Settings construction so the server never accepts a request."""
    with pytest.raises(ValidationError) as exc_info:
        Settings(app_env="production")
    # Error message must name `app_env` so deployers know what to fix.
    assert "app_env=production" in str(exc_info.value)
    assert "JWT_SECRET" in str(exc_info.value)


def test_staging_with_dev_secret_raises() -> None:
    """Same gate as production — staging is shared infra too."""
    with pytest.raises(ValidationError):
        Settings(app_env="staging")


def test_production_with_real_secret_passes() -> None:
    """A genuinely unique 32+ byte secret in prod is the happy path."""
    real_secret = "real-prod-secret-with-enough-entropy-12345"
    settings = Settings(
        app_env="production",
        jwt_secret=SecretStr(real_secret),
    )
    assert settings.jwt_secret.get_secret_value() == real_secret


def test_short_secret_raises_in_any_env() -> None:
    """HS256 best practice is ≥ 32 bytes; the validator enforces it
    regardless of app_env so a dev typo can't ship to prod."""
    for env in ("development", "staging", "production"):
        with pytest.raises(ValidationError) as exc_info:
            Settings(app_env=env, jwt_secret=SecretStr("too-short"))
        assert "32 bytes" in str(exc_info.value)


def test_minimal_length_real_secret_passes() -> None:
    """Exactly 32 bytes is acceptable — boundary check."""
    secret = "a" * 32
    settings = Settings(
        app_env="production",
        jwt_secret=SecretStr(secret),
    )
    assert len(settings.jwt_secret.get_secret_value()) == 32


def test_secret_is_secretstr_so_repr_does_not_leak() -> None:
    """`SecretStr` masks on repr — accidental log of `settings` won't
    expose the raw value the way a plain `str` field would."""
    real_secret = "real-prod-secret-with-enough-entropy-67890"
    settings = Settings(
        app_env="production",
        jwt_secret=SecretStr(real_secret),
    )
    assert real_secret not in repr(settings)
    assert real_secret not in str(settings.jwt_secret)
