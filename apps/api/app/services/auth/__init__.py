"""Phone-auth + JWT issuance — PRD §7.2.

Public surface:
  * `AuthService` + `InvalidCodeError` + dispatcher Protocol
  * `CodeStore` Protocol + `InMemoryCodeStore` / `RedisCodeStore`
  * `RateLimiter` Protocol + `InMemoryRateLimiter` / `RedisRateLimiter`
    + `RateLimited` exception (per PRD §F1 L2 — 60s send cooldown,
    3-strike verify lock for 5 min)
  * `UserRepository` Protocol + `InMemoryUserRepository` + `UserRecord`
  * `TokenPayload` + `mint_token` + `decode_token`
  * `get_auth_service()` factory singleton (wires the above together)
  * `get_current_user_id` dependency (hard-401 since #59)
"""

from functools import lru_cache

import structlog
from redis.asyncio import Redis

from app.config import get_settings
from app.db.session import async_session_factory
from app.services.auth.age import (
    MINOR_AGE_THRESHOLD,
    birthdate_from_year,
    compute_is_minor,
)
from app.services.auth.code_store import (
    CodeStore,
    InMemoryCodeStore,
    RedisCodeStore,
    StoredCode,
)
from app.services.auth.dependency import (
    ANONYMOUS_USER_ID,
    CurrentUser,
    block_minor_quiet_hours,
    get_current_user,
    get_current_user_id,
    require_adult,
    require_age_set,
)
from app.services.auth.email_dispatcher import (
    EmailDispatcher,
    LoggingEmailDispatcher,
    SmtpConfigError,
    SmtpEmailDispatcher,
)
from app.services.auth.email_service import (
    EmailAuthService,
    InvalidEmailCodeError,
)
from app.services.auth.jwt_tokens import TokenPayload, decode_token, mint_token
from app.services.auth.quiet_hours import (
    QUIET_END_HOUR,
    QUIET_START_HOUR,
    is_in_minor_quiet_hours,
)
from app.services.auth.rate_limit import (
    MAX_VERIFY_FAILURES,
    SEND_COOLDOWN,
    VERIFY_LOCK_DURATION,
    InMemoryRateLimiter,
    RateLimited,
    RateLimiter,
    RedisRateLimiter,
)
from app.services.auth.service import (
    AuthService,
    InvalidCodeError,
    LoggingDispatcher,
    ProfileUserNotFoundError,
    SmsDispatcher,
)
from app.services.auth.user_repository import (
    InMemoryUserRepository,
    PostgresUserRepository,
    UserRecord,
    UserRepository,
)

logger = structlog.get_logger(__name__)


@lru_cache(maxsize=1)
def get_auth_service() -> AuthService:
    """Default wiring for `/v1/auth/sms/{send,verify}`.

    All four dependencies are process-wide singletons so a `send`
    followed by a `verify` on the same phone sees the same code store
    AND the same rate-limit counters. The dispatcher is the dev-only
    `LoggingDispatcher`; production will inject a real SMS gateway here.
    """
    code_store = _get_code_store()
    user_repo = _get_user_repository()
    rate_limiter = _get_rate_limiter()
    dispatcher = LoggingDispatcher()
    # H-2 fail-closed: LoggingDispatcher is dev-only. Refuse to wire it
    # in staging/production so a deploy that forgot a real SMS gateway
    # fails at startup instead of logging codes in plaintext.
    _assert_dispatcher_safe_for_env(dispatcher, app_env=get_settings().app_env)
    logger.info(
        "auth_service_wired",
        code_store=code_store.__class__.__name__,
        user_repo=user_repo.__class__.__name__,
        rate_limiter=rate_limiter.__class__.__name__,
        dispatcher=dispatcher.name,
    )
    return AuthService(
        code_store=code_store,
        user_repo=user_repo,
        dispatcher=dispatcher,
        rate_limiter=rate_limiter,
        demo_mode=get_settings().demo_mode,
    )


def _assert_dispatcher_safe_for_env(dispatcher: SmsDispatcher, *, app_env: str) -> None:
    """Fail-closed guard (H-2): reject the dev-only `LoggingDispatcher`
    outside `development`.

    `LoggingDispatcher` writes the SMS verification code to the app log
    in plaintext. That is fine for dev copy-paste, but in staging or
    production it is both an audit failure (PRD §6.2 / NFR S-05 — codes
    are credential-grade and must never hit logs) and functionally
    broken (no real SMS is sent, so users cannot log in).

    Raising here makes a deploy that forgot to inject a real SMS
    gateway fail at startup rather than ship silently. Mirrors the
    `jwt_secret` non-dev validator in `app.config`.
    """
    if app_env != "development" and isinstance(dispatcher, LoggingDispatcher):
        raise RuntimeError(
            "SMS dispatcher is the dev-only LoggingDispatcher but "
            f"app_env={app_env}. LoggingDispatcher writes verification "
            "codes to the application log in plaintext (audit failure) "
            "and sends no real SMS. Inject a production SMS gateway "
            "before deploying outside development."
        )


@lru_cache(maxsize=1)
def get_email_auth_service() -> EmailAuthService:
    """Default wiring for `/v1/auth/email/{send,verify}`.

    Shares `_get_code_store`, `_get_user_repository`, `_get_rate_limiter`
    with the SMS path so a `send` followed by a `verify` on the same
    email sees the same code store AND rate-limit counters. The
    *email dispatcher* is selected via `auth_email_dispatcher_backend`
    and the SMTP_* settings (PR-A1).

    Fail-closed on misconfiguration:
    * `logging` backend outside `app_env=development` raises (mirror
      of `_assert_dispatcher_safe_for_env` for the SMS path).
    * `smtp` backend with any missing SMTP_* env var raises via the
      `SmtpEmailDispatcher` constructor (mirror of the `JWT_SECRET`
      fail-fast validator).

    The email path uses `InMemoryUserRepository` regardless of the
    `auth_repo_backend` setting — PG support for the email column
    lands in PR-A4 (persistence). When the setting is `memory` both
    paths share the same in-memory repo, so an SMS-verified user and
    an email-verified user with the same `user_id` both reach the same
    record on `update_birthdate`.
    """
    settings = get_settings()
    code_store = _get_code_store()
    user_repo = _get_user_repository_for_email()
    rate_limiter = _get_rate_limiter()
    dispatcher = _build_email_dispatcher(settings)
    _assert_email_dispatcher_safe_for_env(dispatcher, app_env=settings.app_env)
    logger.info(
        "email_auth_service_wired",
        code_store=code_store.__class__.__name__,
        user_repo=user_repo.__class__.__name__,
        rate_limiter=rate_limiter.__class__.__name__,
        dispatcher=dispatcher.name,
    )
    return EmailAuthService(
        code_store=code_store,
        user_repo=user_repo,
        dispatcher=dispatcher,
        rate_limiter=rate_limiter,
        demo_mode=settings.demo_mode,
    )


def _build_email_dispatcher(settings: object) -> EmailDispatcher:
    """Construct the email dispatcher selected by env. `settings` is
    typed as `object` to keep `get_settings()` cached value out of the
    public signature — only this module needs to know the field shape."""
    # Local import-style attribute access; `settings` is the Settings
    # instance returned by `get_settings()`. We narrow here so the
    # caller sees a single concrete return.
    backend = settings.auth_email_dispatcher_backend  # type: ignore[attr-defined]
    if backend == "smtp":
        return SmtpEmailDispatcher(
            host=settings.smtp_host,  # type: ignore[attr-defined]
            port=settings.smtp_port,  # type: ignore[attr-defined]
            username=settings.smtp_username,  # type: ignore[attr-defined]
            password=settings.smtp_password.get_secret_value(),  # type: ignore[attr-defined]
            sender=settings.smtp_from,  # type: ignore[attr-defined]
            starttls=settings.smtp_starttls,  # type: ignore[attr-defined]
        )
    return LoggingEmailDispatcher()


def _assert_email_dispatcher_safe_for_env(dispatcher: EmailDispatcher, *, app_env: str) -> None:
    """Fail-closed guard: refuse to wire `LoggingEmailDispatcher`
    outside `app_env=development`. Verification codes in plaintext
    in app logs are an audit failure (PRD §6.2 / NFR S-05); this
    raise here makes a deploy that forgot to set
    `AUTH_EMAIL_DISPATCHER_BACKEND=smtp` fail at startup instead of
    silently shipping codes into the log."""
    if app_env != "development" and isinstance(dispatcher, LoggingEmailDispatcher):
        raise RuntimeError(
            "Email dispatcher is the dev-only LoggingEmailDispatcher but "
            f"app_env={app_env}. LoggingEmailDispatcher writes verification "
            "codes to the application log in plaintext (audit failure) "
            "and sends no real email. Set AUTH_EMAIL_DISPATCHER_BACKEND=smtp "
            "and the SMTP_* envs before deploying outside development."
        )


def _get_user_repository_for_email() -> UserRepository:
    """Email auth path always uses the in-memory user repo for now —
    the PG schema doesn't carry an `email` column yet (PR-A4). When
    `auth_repo_backend=memory` this is the same singleton as the SMS
    path; under `postgres` we still hand back an in-memory repo here
    so email users have somewhere to live until persistence lands.
    A startup log line records the divergence."""
    settings = get_settings()
    if settings.auth_repo_backend == "memory":
        return _get_user_repository()
    logger.warning(
        "email_user_repo_forced_memory",
        configured_backend=settings.auth_repo_backend,
        reason=(
            "PG email column lands in PR-A4; email auth runs against "
            "the in-memory repo even when SMS uses Postgres."
        ),
    )
    return _email_user_repository_memory_singleton()


@lru_cache(maxsize=1)
def _email_user_repository_memory_singleton() -> UserRepository:
    return InMemoryUserRepository()


@lru_cache(maxsize=1)
def _get_code_store() -> CodeStore:
    """Backend chosen via settings; `memory` for dev / tests, `redis`
    in production so codes survive a restart and one-shot pop is
    atomic across concurrent verify calls."""
    settings = get_settings()
    if settings.auth_code_store_backend == "redis":
        logger.info("code_store_wired", backend="redis", url=settings.redis_url)
        # `from_url` returns a lazily-connecting client; the first await
        # call actually opens the connection, which matches our other
        # singletons (no startup cost just to import the module).
        return RedisCodeStore(Redis.from_url(settings.redis_url))
    logger.info("code_store_wired", backend="memory")
    return InMemoryCodeStore()


@lru_cache(maxsize=1)
def _get_user_repository() -> UserRepository:
    """Backend chosen via settings; `memory` for dev / tests, `postgres`
    when running against a real DB with alembic applied."""
    backend = get_settings().auth_repo_backend
    if backend == "postgres":
        logger.info("user_repository_wired", backend="postgres")
        return PostgresUserRepository(async_session_factory)
    logger.info("user_repository_wired", backend="memory")
    return InMemoryUserRepository()


@lru_cache(maxsize=1)
def _get_rate_limiter() -> RateLimiter:
    """Piggybacks on the same `auth_code_store_backend` setting as the
    code store: if you have Redis configured for codes, you also have
    it for rate-limit counters. Avoids a second config knob with
    obvious "should be the same" semantics.
    """
    settings = get_settings()
    if settings.auth_code_store_backend == "redis":
        logger.info("rate_limiter_wired", backend="redis", url=settings.redis_url)
        return RedisRateLimiter(Redis.from_url(settings.redis_url))
    logger.info("rate_limiter_wired", backend="memory")
    return InMemoryRateLimiter()


__all__ = [
    "ANONYMOUS_USER_ID",
    "MAX_VERIFY_FAILURES",
    "MINOR_AGE_THRESHOLD",
    "QUIET_END_HOUR",
    "QUIET_START_HOUR",
    "SEND_COOLDOWN",
    "VERIFY_LOCK_DURATION",
    "AuthService",
    "CodeStore",
    "CurrentUser",
    "EmailAuthService",
    "EmailDispatcher",
    "InMemoryCodeStore",
    "InMemoryRateLimiter",
    "InMemoryUserRepository",
    "InvalidCodeError",
    "InvalidEmailCodeError",
    "LoggingDispatcher",
    "LoggingEmailDispatcher",
    "PostgresUserRepository",
    "ProfileUserNotFoundError",
    "RateLimited",
    "RateLimiter",
    "RedisCodeStore",
    "RedisRateLimiter",
    "SmsDispatcher",
    "SmtpConfigError",
    "SmtpEmailDispatcher",
    "StoredCode",
    "TokenPayload",
    "UserRecord",
    "UserRepository",
    "birthdate_from_year",
    "block_minor_quiet_hours",
    "compute_is_minor",
    "decode_token",
    "get_auth_service",
    "get_current_user",
    "get_current_user_id",
    "get_email_auth_service",
    "is_in_minor_quiet_hours",
    "mint_token",
    "require_adult",
    "require_age_set",
]
