"""Auth endpoints — PRD §7.2.

Two routes:
  * POST /v1/auth/sms/send   — request a 6-digit verification code.
  * POST /v1/auth/sms/verify — verify the code and mint a JWT.

Both endpoints surface the per-phone rate limit (PRD §F1 L2) as a
429 with `Retry-After: <seconds>` per RFC 6585. `Retry-After` flows
through the global `_http_exception` handler in `main.py` because the
handler forwards `HTTPException.headers`. Two stable error codes so
the frontend can pick distinct copy:

  * `SMS_SEND_COOLDOWN`  — 60s send cooldown (post-`/send`)
  * `SMS_VERIFY_LOCKED`  — 3-strike verify lock (5 min)
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status

from app.schemas.auth import (
    SmsSendRequest,
    SmsSendResponse,
    SmsVerifyRequest,
    SmsVerifyResponse,
)
from app.services.auth import (
    AuthService,
    InvalidCodeError,
    RateLimited,
    get_auth_service,
)

router = APIRouter(prefix="/auth", tags=["auth"])


_RATE_LIMIT_RESPONSES = {
    429: {
        "description": (
            "Per-phone rate limit fired. `code` is `SMS_SEND_COOLDOWN` for the "
            "send-after-send case and `SMS_VERIFY_LOCKED` for the "
            "3-failure verify lock. The `Retry-After` header carries the "
            "seconds the client must wait."
        ),
    },
}


@router.post(
    "/sms/send",
    response_model=SmsSendResponse,
    summary="Request an SMS verification code",
    responses=_RATE_LIMIT_RESPONSES,
)
async def sms_send(
    payload: SmsSendRequest,
    service: AuthService = Depends(get_auth_service),
) -> SmsSendResponse:
    try:
        return await service.send_code(payload.phone)
    except RateLimited as exc:
        raise _rate_limit_to_http(exc) from exc


@router.post(
    "/sms/verify",
    response_model=SmsVerifyResponse,
    summary="Verify SMS code and mint a JWT",
    responses={
        400: {"description": "Code expired, never sent, or does not match"},
        **_RATE_LIMIT_RESPONSES,
    },
)
async def sms_verify(
    payload: SmsVerifyRequest,
    service: AuthService = Depends(get_auth_service),
) -> SmsVerifyResponse:
    try:
        return await service.verify_code(payload.phone, payload.code)
    except RateLimited as exc:
        raise _rate_limit_to_http(exc) from exc
    except InvalidCodeError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"code": "INVALID_CODE", "message": str(exc)},
        ) from exc


def _rate_limit_to_http(exc: RateLimited) -> HTTPException:
    """Map a `RateLimited` to 429 + `Retry-After`. Two `code` values so
    the frontend can render different copy ("还剩 X 秒可重发" vs
    "输错次数过多，X 秒后再试")."""
    if exc.kind == "send_cooldown":
        code = "SMS_SEND_COOLDOWN"
        message = f"短信验证码 {exc.retry_after_seconds} 秒后才能再发"
    else:
        code = "SMS_VERIFY_LOCKED"
        message = f"输错次数过多，{exc.retry_after_seconds} 秒后再试"
    return HTTPException(
        status_code=status.HTTP_429_TOO_MANY_REQUESTS,
        detail={"code": code, "message": message},
        headers={"Retry-After": str(exc.retry_after_seconds)},
    )
