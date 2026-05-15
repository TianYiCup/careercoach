"""`AuthService` — orchestrates the phone-auth flow.

End-to-end flows:

    POST /v1/auth/sms/send    → service.send_code(phone)
                              → CodeStore.set(phone, code, ttl=300s)
                              → log "sms_dispatched" (real SMS provider in a later PR)
                              → return SmsSendResponse(ttl=60)  # client-side resend cooldown

    POST /v1/auth/sms/verify  → service.verify_code(phone, code)
                              → CodeStore.pop(phone)            # one-shot retire
                              → UserRepository.get_or_create
                              → mint_token(user_id, persona_type, is_minor)
                              → return SmsVerifyResponse

v0 doesn't talk to a real SMS gateway — codes are generated server-side
and logged. The `SmsDispatcher` Protocol lives here so the future Aliyun
/ Tencent integration drops into the same slot without service changes.
"""

from __future__ import annotations

import secrets
from datetime import UTC, datetime, timedelta
from typing import Protocol, runtime_checkable

import structlog

from app.schemas.auth import SmsSendResponse, SmsVerifyResponse, UserPublic
from app.services.auth.age import birthdate_from_year, compute_is_minor
from app.services.auth.code_store import CodeStore
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

# 5-minute window matches design-spec §7.2 — long enough that an SMS
# delay doesn't lock users out, short enough to bound abuse blast.
_CODE_TTL = timedelta(minutes=5)

# Resend cooldown surfaced to the client. The actual server-side
# expiry is `_CODE_TTL`; this is just the UI debounce hint.
_RESEND_COOLDOWN_SECONDS = 60

# v0 default identity — every user starts as in_school until the
# profile flow (later PR) lets them switch.
_DEFAULT_PERSONA = "in_school"
_DEFAULT_NICKNAME_TEMPLATE = "K 学员 {tail}"


class InvalidCodeError(RuntimeError):
    """Route maps to 400 — supplied code doesn't match or has expired."""


class ProfileUserNotFoundError(RuntimeError):
    """Route maps to 404 — JWT subject doesn't correspond to any user
    (deleted account, stale token after DB wipe, malformed id)."""


@runtime_checkable
class SmsDispatcher(Protocol):
    """Pluggable SMS gateway. v0 ships a LoggingDispatcher; production
    swaps in Aliyun / Tencent without service code changes."""

    name: str

    async def send(self, *, phone: str, code: str) -> None: ...


class LoggingDispatcher:
    """No-op dispatcher that just logs the code so dev can copy-paste
    it from stdout. NEVER swap this in for prod — codes in plaintext
    in app logs are an audit-fail."""

    name: str = "logging"

    async def send(self, *, phone: str, code: str) -> None:
        logger.info(
            "sms_dispatched_via_log",
            phone=_mask_phone(phone),
            code=code,
            note="dev-only dispatcher; do not use in production",
        )


class AuthService:
    """Owns code generation, dispatch, verification, and JWT minting.

    Per-phone rate limit policy (PRD §F1 L2) is enforced here, in front
    of `CodeStore` and the dispatcher, so an attacker can't drive any
    side-effects (SMS dispatch, code-store writes, repo lookups) by
    spamming the API:

      * `send_code` checks `RateLimiter.get_send_cooldown_seconds`
        before doing anything; rejects with `RateLimited(send_cooldown)`
        if blocked. On success, marks the phone for `SEND_COOLDOWN`.
      * `verify_code` checks `get_verify_lock_seconds` before touching
        `CodeStore.pop`; rejects with `RateLimited(verify_locked)` if
        the phone is locked. Every wrong code (or "no pending code")
        increments the failure counter; the 3rd failure sets the lock
        for `VERIFY_LOCK_DURATION`. A successful verify resets both.
    """

    def __init__(
        self,
        *,
        code_store: CodeStore,
        user_repo: UserRepository,
        dispatcher: SmsDispatcher,
        rate_limiter: RateLimiter,
    ) -> None:
        self._code_store = code_store
        self._user_repo = user_repo
        self._dispatcher = dispatcher
        self._rate_limiter = rate_limiter

    async def send_code(self, phone: str) -> SmsSendResponse:
        cooldown_left = await self._rate_limiter.get_send_cooldown_seconds(phone)
        if cooldown_left is not None:
            logger.info(
                "sms_send_rate_limited",
                phone=_mask_phone(phone),
                seconds_left=cooldown_left,
            )
            raise RateLimited(kind="send_cooldown", retry_after_seconds=cooldown_left)

        code = _generate_code()
        await self._code_store.set(phone, code, ttl=_CODE_TTL)
        await self._dispatcher.send(phone=phone, code=code)
        await self._rate_limiter.mark_send(phone, cooldown=SEND_COOLDOWN)
        logger.info(
            "sms_send_requested",
            phone=_mask_phone(phone),
            dispatcher=self._dispatcher.name,
            ttl_seconds=int(_CODE_TTL.total_seconds()),
        )
        return SmsSendResponse(ttl=_RESEND_COOLDOWN_SECONDS)

    async def verify_code(self, phone: str, code: str) -> SmsVerifyResponse:
        lock_left = await self._rate_limiter.get_verify_lock_seconds(phone)
        if lock_left is not None:
            logger.info(
                "sms_verify_locked",
                phone=_mask_phone(phone),
                seconds_left=lock_left,
            )
            raise RateLimited(kind="verify_locked", retry_after_seconds=lock_left)

        stored = await self._code_store.pop(phone)
        if stored is None or not secrets.compare_digest(stored, code):
            await self._record_verify_failure(phone, has_pending=stored is not None)
            if stored is None:
                raise InvalidCodeError("code expired or never requested")
            raise InvalidCodeError("code does not match")

        user = await self._upsert_user(phone)
        # Returning users who already declared their birth_year keep
        # the age-gate-cleared flag on the fresh token; first-time
        # users start with age_set=False and the next request to a
        # gated endpoint will 403 AGE_REQUIRED, prompting the client
        # to call `POST /v1/users/me/birth-year`.
        token = mint_token(
            user_id=user.user_id,
            persona_type=user.persona_type,
            is_minor=user.is_minor,
            age_set=user.birthdate is not None,
        )

        # Reset failure state AFTER mint succeeds so a downstream error
        # (e.g. user repo timeout) doesn't leave the phone unlocked
        # without a confirmed login.
        await self._rate_limiter.reset_verify_state(phone)

        logger.info(
            "sms_verify_succeeded",
            user_id=user.user_id,
            phone=_mask_phone(phone),
            persona_type=user.persona_type,
        )
        return SmsVerifyResponse(
            token=token,
            user=UserPublic(
                id=user.user_id,
                nickname=user.nickname,
                persona_type=user.persona_type,
                is_minor=user.is_minor,
            ),
        )

    async def _record_verify_failure(self, phone: str, *, has_pending: bool) -> None:
        """Increment the failure counter and log. `has_pending` is just
        for log discipline so we can distinguish "wrong code" from "no
        code in flight" in production logs — both count as failures
        for the lockout to keep the attack surface from being skipped
        by an attacker who doesn't bother calling /send first.
        """
        count = await self._rate_limiter.record_verify_failure(
            phone,
            max_failures=MAX_VERIFY_FAILURES,
            lock_duration=VERIFY_LOCK_DURATION,
        )
        if has_pending:
            logger.info(
                "sms_verify_mismatch",
                phone=_mask_phone(phone),
                failure_count=count,
            )
        else:
            logger.info(
                "sms_verify_no_pending_code",
                phone=_mask_phone(phone),
                failure_count=count,
            )

    async def _upsert_user(self, phone: str) -> UserRecord:
        existing = await self._user_repo.get_by_phone(phone)
        if existing is not None:
            return existing
        return await self._user_repo.create(
            phone=phone,
            nickname=_DEFAULT_NICKNAME_TEMPLATE.format(tail=phone[-4:]),
            persona_type=_DEFAULT_PERSONA,
            is_minor=False,
        )

    async def update_birth_year(
        self,
        user_id: str,
        *,
        birth_year: int,
        today: datetime | None = None,
    ) -> SmsVerifyResponse:
        """Set the caller's `birthdate` (proxied from `birth_year`) and
        mint a fresh JWT carrying the recomputed `is_minor` flag.

        Returns a `SmsVerifyResponse` shape so the client doesn't need
        a second schema: same `{user, token}` envelope as the SMS verify
        flow, just minted from a profile-update trigger rather than a
        code-verify trigger.

        Raises `ProfileUserNotFoundError` if the JWT subject doesn't
        match any user — the route maps that to 404. This shouldn't
        happen for a valid token (sub came from our own user repo) but
        a deleted user or a stale token from a wiped DB would land
        here and we don't want a 500.
        """
        anchor = today or datetime.now(UTC)
        birthdate = birthdate_from_year(birth_year)
        is_minor = compute_is_minor(birthdate, anchor.date())
        try:
            record = await self._user_repo.update_birthdate(
                user_id,
                birthdate=birthdate,
                is_minor=is_minor,
            )
        except KeyError as exc:
            raise ProfileUserNotFoundError(user_id) from exc

        # birth_year is now set, so age_set=True on every mint here —
        # the route layer's age gate stops firing for this user.
        token = mint_token(
            user_id=record.user_id,
            persona_type=record.persona_type,
            is_minor=record.is_minor,
            age_set=True,
        )
        logger.info(
            "user_birth_year_updated",
            user_id=_mask_user_id(record.user_id),
            birth_year=birth_year,
            is_minor=record.is_minor,
        )
        return SmsVerifyResponse(
            token=token,
            user=UserPublic(
                id=record.user_id,
                nickname=record.nickname,
                persona_type=record.persona_type,
                is_minor=record.is_minor,
            ),
        )


def _generate_code() -> str:
    """Six-digit zero-padded code. `secrets.randbelow` is the
    constant-time source — `random` is not safe for auth codes."""
    return f"{secrets.randbelow(1_000_000):06d}"


def _mask_phone(phone: str) -> str:
    """Mask middle 4 digits — log discipline (PRD §6.2)."""
    if len(phone) < 7:
        return "***"
    return f"{phone[:3]}****{phone[-4:]}"


def _mask_user_id(user_id: str) -> str:
    """Truncate the UUID body to first 8 chars for log discipline."""
    if len(user_id) <= 10:
        return user_id
    return f"{user_id[:10]}…"


__all__ = [
    "AuthService",
    "InvalidCodeError",
    "LoggingDispatcher",
    "ProfileUserNotFoundError",
    "SmsDispatcher",
]
