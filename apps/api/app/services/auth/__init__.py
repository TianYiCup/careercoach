"""Phone-auth + JWT issuance — PRD §7.2.

Public surface:
  * `AuthService` + `InvalidCodeError` + dispatcher Protocol
  * `CodeStore` Protocol + `InMemoryCodeStore`
  * `UserRepository` Protocol + `InMemoryUserRepository` + `UserRecord`
  * `TokenPayload` + `mint_token` + `decode_token`
  * `get_auth_service()` factory singleton

JWT enforcement on other routes is a follow-up PR — this one ships
the issuance side. Routes still treat the user as anonymous until
the middleware lands.
"""

from functools import lru_cache

import structlog

from app.config import get_settings
from app.db.session import async_session_factory
from app.services.auth.code_store import (
    CodeStore,
    InMemoryCodeStore,
    StoredCode,
)
from app.services.auth.dependency import ANONYMOUS_USER_ID, get_current_user_id
from app.services.auth.jwt_tokens import TokenPayload, decode_token, mint_token
from app.services.auth.service import (
    AuthService,
    InvalidCodeError,
    LoggingDispatcher,
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

    All three dependencies are process-wide singletons so a `send`
    followed by a `verify` on the same phone sees the same code store.
    The dispatcher is the dev-only `LoggingDispatcher`; production
    will inject a real SMS gateway here.
    """
    code_store = _get_code_store()
    user_repo = _get_user_repository()
    dispatcher = LoggingDispatcher()
    logger.info(
        "auth_service_wired",
        code_store=code_store.__class__.__name__,
        user_repo=user_repo.__class__.__name__,
        dispatcher=dispatcher.name,
    )
    return AuthService(
        code_store=code_store,
        user_repo=user_repo,
        dispatcher=dispatcher,
    )


@lru_cache(maxsize=1)
def _get_code_store() -> InMemoryCodeStore:
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


__all__ = [
    "ANONYMOUS_USER_ID",
    "AuthService",
    "CodeStore",
    "InMemoryCodeStore",
    "InMemoryUserRepository",
    "InvalidCodeError",
    "LoggingDispatcher",
    "PostgresUserRepository",
    "SmsDispatcher",
    "StoredCode",
    "TokenPayload",
    "UserRecord",
    "UserRepository",
    "decode_token",
    "get_auth_service",
    "get_current_user_id",
    "mint_token",
]
