"""`EmailAuthService` — orchestrates the email-auth flow.

Parallel to `AuthService` (SMS path in `service.py`). End-to-end flows:

    POST /v1/auth/email/send    → service.send_code(email)
                                → CodeStore.set(email, code, ttl=300s)
                                → EmailDispatcher.send(email, code)
                                → return EmailSendResponse(ttl=60)

    POST /v1/auth/email/verify  → service.verify_code(email, code)
                                → CodeStore.pop(email)
                                → UserRepository.get_or_create email user
                                → mint_token(user_id, persona_type, is_minor)
                                → return EmailVerifyResponse

This service is intentionally a near-copy of `AuthService` rather than
a refactor of it. The SMS path is shipped and tested; the email path
ships under time pressure, and a parallel class keeps the SMS path
risk-free. PR-A4 (persistence) will revisit the duplication after
both paths land.

The `CodeStore` + `RateLimiter` are *shared* with the SMS path via
dict-key isolation: email keys are full address strings (e.g.
`alex@example.com`), SMS keys are 11-digit phone strings. The two
namespaces don't collide, so re-using the singletons just works.
"""

from __future__ import annotations

import secrets
from datetime import timedelta

import structlog

from app.schemas.auth import UserPublic
from app.schemas.email_auth import EmailSendResponse, EmailVerifyResponse
from app.services.auth.code_store import CodeStore
from app.services.auth.email_dispatcher import EmailDispatcher, _mask_email
from app.services.auth.jwt_tokens import mint_token
from app.services.auth.rate_limit import (
    MAX_VERIFY_FAILURES,
    SEND_COOLDOWN,
    VERIFY_LOCK_DURATION,
    RateLimited,
    RateLimiter,
)
from app.services.auth.user_repository import UserRecord, UserRepository

logger = structlog.get_logger(__name__)

# Mirrors `_CODE_TTL` in the SMS service — keeping the windows identical
# avoids cross-channel surprise ("my email code expired in 2 minutes but
# my SMS code didn't").
_CODE_TTL = timedelta(minutes=5)
_RESEND_COOLDOWN_SECONDS = 60

_DEFAULT_PERSONA = "in_school"
# `<local>` of the email becomes the nickname seed — e.g.
# `alex@example.com` -> "K 学员 alex". Capped to keep DB-friendly even
# though the InMemory repo doesn't enforce it (PG `nickname` is
# `String(64)`).
_DEFAULT_NICKNAME_TEMPLATE = "K 学员 {tail}"
_NICKNAME_TAIL_MAX = 32


class InvalidEmailCodeError(RuntimeError):
    """Route maps to 400 — supplied code doesn't match or has expired.
    Mirror of `InvalidCodeError` for the SMS path."""


class EmailAuthService:
    """Email-channel verification-code auth.

    Per-recipient rate-limit policy matches the SMS path (PRD §F1 L2):
      * 60-second send cooldown
      * 3-strike verify lock for 5 minutes
    Counter keys are the email address; the rate-limiter doesn't care
    that they aren't phone numbers.
    """

    def __init__(
        self,
        *,
        code_store: CodeStore,
        user_repo: UserRepository,
        dispatcher: EmailDispatcher,
        rate_limiter: RateLimiter,
    ) -> None:
        self._code_store = code_store
        self._user_repo = user_repo
        self._dispatcher = dispatcher
        self._rate_limiter = rate_limiter

    async def send_code(self, email: str) -> EmailSendResponse:
        normalised = _normalise(email)
        cooldown_left = await self._rate_limiter.get_send_cooldown_seconds(normalised)
        if cooldown_left is not None:
            logger.info(
                "email_send_rate_limited",
                email=_mask_email(normalised),
                seconds_left=cooldown_left,
            )
            raise RateLimited(kind="send_cooldown", retry_after_seconds=cooldown_left)

        code = _generate_code()
        await self._code_store.set(normalised, code, ttl=_CODE_TTL)
        await self._dispatcher.send(email=normalised, code=code)
        await self._rate_limiter.mark_send(normalised, cooldown=SEND_COOLDOWN)
        logger.info(
            "email_send_requested",
            email=_mask_email(normalised),
            dispatcher=self._dispatcher.name,
            ttl_seconds=int(_CODE_TTL.total_seconds()),
        )
        return EmailSendResponse(ttl=_RESEND_COOLDOWN_SECONDS)

    async def verify_code(self, email: str, code: str) -> EmailVerifyResponse:
        normalised = _normalise(email)
        lock_left = await self._rate_limiter.get_verify_lock_seconds(normalised)
        if lock_left is not None:
            logger.info(
                "email_verify_locked",
                email=_mask_email(normalised),
                seconds_left=lock_left,
            )
            raise RateLimited(kind="verify_locked", retry_after_seconds=lock_left)

        stored = await self._code_store.pop(normalised)
        if stored is None or not secrets.compare_digest(stored, code):
            await self._record_verify_failure(normalised, has_pending=stored is not None)
            if stored is None:
                raise InvalidEmailCodeError("code expired or never requested")
            raise InvalidEmailCodeError("code does not match")

        user = await self._upsert_user(normalised)
        token = mint_token(
            user_id=user.user_id,
            persona_type=user.persona_type,
            is_minor=user.is_minor,
            age_set=user.birthdate is not None,
        )
        await self._rate_limiter.reset_verify_state(normalised)
        logger.info(
            "email_verify_succeeded",
            user_id=user.user_id,
            email=_mask_email(normalised),
            persona_type=user.persona_type,
        )
        return EmailVerifyResponse(
            token=token,
            user=UserPublic(
                id=user.user_id,
                nickname=user.nickname,
                persona_type=user.persona_type,
                is_minor=user.is_minor,
            ),
        )

    async def _record_verify_failure(self, email: str, *, has_pending: bool) -> None:
        count = await self._rate_limiter.record_verify_failure(
            email,
            max_failures=MAX_VERIFY_FAILURES,
            lock_duration=VERIFY_LOCK_DURATION,
        )
        event = "email_verify_mismatch" if has_pending else "email_verify_no_pending_code"
        logger.info(event, email=_mask_email(email), failure_count=count)

    async def _upsert_user(self, email: str) -> UserRecord:
        existing = await self._user_repo.get_by_email(email)
        if existing is not None:
            return existing
        nickname = _default_nickname(email)
        return await self._user_repo.create_email_user(
            email=email,
            nickname=nickname,
            persona_type=_DEFAULT_PERSONA,
            is_minor=False,
        )


def _normalise(email: str) -> str:
    """Email addresses are case-insensitive for the local part on most
    providers in practice (RFC 5321 §2.4 leaves this to the host but
    nobody honours case sensitivity in 2026). Lowercasing keeps
    `Alex@example.com` and `alex@example.com` from forking into two
    separate accounts."""
    return email.strip().lower()


def _default_nickname(email: str) -> str:
    """Seed the nickname from the email's local part, truncated."""
    local = email.split("@", 1)[0][:_NICKNAME_TAIL_MAX]
    return _DEFAULT_NICKNAME_TEMPLATE.format(tail=local or "新人")


def _generate_code() -> str:
    """Six-digit zero-padded code. Same constant-time source as the
    SMS service — `random` is not safe for auth codes."""
    return f"{secrets.randbelow(1_000_000):06d}"


__all__ = [
    "EmailAuthService",
    "InvalidEmailCodeError",
]
