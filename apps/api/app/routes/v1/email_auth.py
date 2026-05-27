"""Email-auth endpoints — PR-A2 (CareerCoach foundation §F1).

Two routes parallel to the SMS path (`app.routes.v1.auth`):
  * POST /v1/auth/email/send   — request a 6-digit verification code.
  * POST /v1/auth/email/verify — verify the code and mint a JWT.

Same rate-limit shape as the SMS path (PRD §F1 L2) — 429 with
`Retry-After`, two stable codes so the frontend can render distinct
copy:

  * `EMAIL_SEND_COOLDOWN`  — 60s send cooldown (post-`/send`)
  * `EMAIL_VERIFY_LOCKED`  — 3-strike verify lock (5 min)

`POST /send` reuses the existing CodeStore + RateLimiter singletons
(see `get_email_auth_service`); the per-recipient key namespaces don't
collide with the SMS path because email keys contain `@` and SMS keys
are 11-digit numerics.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status

from app.schemas.email_auth import (
    EmailSendRequest,
    EmailSendResponse,
    EmailVerifyRequest,
    EmailVerifyResponse,
)
from app.services.auth import (
    EmailAuthService,
    InvalidEmailCodeError,
    RateLimited,
    get_email_auth_service,
)

router = APIRouter(prefix="/auth", tags=["auth"])


_RATE_LIMIT_RESPONSES = {
    429: {
        "description": (
            "Per-email rate limit fired. `code` is `EMAIL_SEND_COOLDOWN` for "
            "the send-after-send case and `EMAIL_VERIFY_LOCKED` for the "
            "3-failure verify lock. The `Retry-After` header carries the "
            "seconds the client must wait."
        ),
    },
}


@router.post(
    "/email/send",
    response_model=EmailSendResponse,
    summary="Request an email verification code",
    responses={
        502: {"description": "Email gateway temporarily unavailable — client should retry."},
        **_RATE_LIMIT_RESPONSES,
    },
)
async def email_send(
    payload: EmailSendRequest,
    service: EmailAuthService = Depends(get_email_auth_service),
) -> EmailSendResponse:
    try:
        return await service.send_code(payload.email)
    except RateLimited as exc:
        raise _rate_limit_to_http(exc) from exc
    except RuntimeError as exc:
        # SmtpEmailDispatcher wraps aiosmtplib failures as
        # RuntimeError("email dispatch failed") so we can surface a
        # single 502 here. Any other RuntimeError reaches the global
        # 500 handler — this branch is narrow on purpose.
        if str(exc) == "email dispatch failed":
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail={
                    "code": "EMAIL_DISPATCH_FAILED",
                    "message": "邮件发送暂时失败，请稍后再试",
                },
            ) from exc
        raise


@router.post(
    "/email/verify",
    response_model=EmailVerifyResponse,
    summary="Verify email code and mint a JWT",
    responses={
        400: {"description": "Code expired, never sent, or does not match"},
        **_RATE_LIMIT_RESPONSES,
    },
)
async def email_verify(
    payload: EmailVerifyRequest,
    service: EmailAuthService = Depends(get_email_auth_service),
) -> EmailVerifyResponse:
    try:
        return await service.verify_code(payload.email, payload.code)
    except RateLimited as exc:
        raise _rate_limit_to_http(exc) from exc
    except InvalidEmailCodeError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"code": "INVALID_CODE", "message": str(exc)},
        ) from exc


def _rate_limit_to_http(exc: RateLimited) -> HTTPException:
    """Map a `RateLimited` to 429 + `Retry-After`. Two `code` values so
    the frontend can render different copy — mirror of the SMS path
    `_rate_limit_to_http` in `auth.py`."""
    if exc.kind == "send_cooldown":
        code = "EMAIL_SEND_COOLDOWN"
        message = f"邮箱验证码 {exc.retry_after_seconds} 秒后才能再发"
    else:
        code = "EMAIL_VERIFY_LOCKED"
        message = f"输错次数过多，{exc.retry_after_seconds} 秒后再试"
    return HTTPException(
        status_code=status.HTTP_429_TOO_MANY_REQUESTS,
        detail={"code": code, "message": message},
        headers={"Retry-After": str(exc.retry_after_seconds)},
    )
