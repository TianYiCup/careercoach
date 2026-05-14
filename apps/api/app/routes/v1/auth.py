"""Auth endpoints — PRD §7.2.

Both routes flip from 501 to real handlers in this PR. The actual
JWT enforcement on other routes (Depends(get_current_user)) lands
in a follow-up; this PR only ships the issuance side.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status

from app.schemas.auth import (
    SmsSendRequest,
    SmsSendResponse,
    SmsVerifyRequest,
    SmsVerifyResponse,
)
from app.services.auth import AuthService, InvalidCodeError, get_auth_service

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post(
    "/sms/send",
    response_model=SmsSendResponse,
    summary="Request an SMS verification code",
)
async def sms_send(
    payload: SmsSendRequest,
    service: AuthService = Depends(get_auth_service),
) -> SmsSendResponse:
    return await service.send_code(payload.phone)


@router.post(
    "/sms/verify",
    response_model=SmsVerifyResponse,
    summary="Verify SMS code and mint a JWT",
    responses={
        400: {"description": "Code expired, never sent, or does not match"},
    },
)
async def sms_verify(
    payload: SmsVerifyRequest,
    service: AuthService = Depends(get_auth_service),
) -> SmsVerifyResponse:
    try:
        return await service.verify_code(payload.phone, payload.code)
    except InvalidCodeError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"code": "INVALID_CODE", "message": str(exc)},
        ) from exc
