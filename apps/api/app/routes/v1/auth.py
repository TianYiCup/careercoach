"""Auth endpoints — PRD §7.2."""

from fastapi import APIRouter

from app.routes.v1._stub import STUB_RESPONSES, not_implemented
from app.schemas.auth import (
    SmsSendRequest,
    SmsSendResponse,
    SmsVerifyRequest,
    SmsVerifyResponse,
)

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post(
    "/sms/send",
    response_model=SmsSendResponse,
    responses=STUB_RESPONSES,
    summary="Request an SMS verification code",
)
async def sms_send(payload: SmsSendRequest) -> SmsSendResponse:
    raise not_implemented("POST /v1/auth/sms/send")


@router.post(
    "/sms/verify",
    response_model=SmsVerifyResponse,
    responses=STUB_RESPONSES,
    summary="Verify SMS code and mint a JWT",
)
async def sms_verify(payload: SmsVerifyRequest) -> SmsVerifyResponse:
    raise not_implemented("POST /v1/auth/sms/verify")
