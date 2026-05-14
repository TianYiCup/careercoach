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
from datetime import timedelta
from typing import Protocol, runtime_checkable

import structlog

from app.schemas.auth import SmsSendResponse, SmsVerifyResponse, UserPublic
from app.services.auth.code_store import CodeStore
from app.services.auth.jwt_tokens import mint_token
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
    """Owns code generation, dispatch, verification, and JWT minting."""

    def __init__(
        self,
        *,
        code_store: CodeStore,
        user_repo: UserRepository,
        dispatcher: SmsDispatcher,
    ) -> None:
        self._code_store = code_store
        self._user_repo = user_repo
        self._dispatcher = dispatcher

    async def send_code(self, phone: str) -> SmsSendResponse:
        code = _generate_code()
        await self._code_store.set(phone, code, ttl=_CODE_TTL)
        await self._dispatcher.send(phone=phone, code=code)
        logger.info(
            "sms_send_requested",
            phone=_mask_phone(phone),
            dispatcher=self._dispatcher.name,
            ttl_seconds=int(_CODE_TTL.total_seconds()),
        )
        return SmsSendResponse(ttl=_RESEND_COOLDOWN_SECONDS)

    async def verify_code(self, phone: str, code: str) -> SmsVerifyResponse:
        stored = await self._code_store.pop(phone)
        if stored is None:
            logger.info("sms_verify_no_pending_code", phone=_mask_phone(phone))
            raise InvalidCodeError("code expired or never requested")
        if not secrets.compare_digest(stored, code):
            logger.info("sms_verify_mismatch", phone=_mask_phone(phone))
            raise InvalidCodeError("code does not match")

        user = await self._upsert_user(phone)
        token = mint_token(
            user_id=user.user_id,
            persona_type=user.persona_type,
            is_minor=user.is_minor,
        )

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


def _generate_code() -> str:
    """Six-digit zero-padded code. `secrets.randbelow` is the
    constant-time source — `random` is not safe for auth codes."""
    return f"{secrets.randbelow(1_000_000):06d}"


def _mask_phone(phone: str) -> str:
    """Mask middle 4 digits — log discipline (PRD §6.2)."""
    if len(phone) < 7:
        return "***"
    return f"{phone[:3]}****{phone[-4:]}"


__all__ = [
    "AuthService",
    "InvalidCodeError",
    "LoggingDispatcher",
    "SmsDispatcher",
]
