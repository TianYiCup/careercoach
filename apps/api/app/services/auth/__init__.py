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
    get_current_user,
    get_current_user_id,
    require_adult,
    require_age_set,
)
from app.services.auth.jwt_tokens import TokenPayload, decode_token, mint_token
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
    )


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
    "SEND_COOLDOWN",
    "VERIFY_LOCK_DURATION",
    "AuthService",
    "CodeStore",
    "CurrentUser",
    "InMemoryCodeStore",
    "InMemoryRateLimiter",
    "InMemoryUserRepository",
    "InvalidCodeError",
    "LoggingDispatcher",
    "PostgresUserRepository",
    "ProfileUserNotFoundError",
    "RateLimited",
    "RateLimiter",
    "RedisCodeStore",
    "RedisRateLimiter",
    "SmsDispatcher",
    "StoredCode",
    "TokenPayload",
    "UserRecord",
    "UserRepository",
    "birthdate_from_year",
    "compute_is_minor",
    "decode_token",
    "get_auth_service",
    "get_current_user",
    "get_current_user_id",
    "mint_token",
    "require_adult",
    "require_age_set",
]
