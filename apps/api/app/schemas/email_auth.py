"""Email-auth schemas (CareerCoach foundation §F1 — PR-A2).

Parallel to `app.schemas.auth` (SMS path). Shipped together because
ICP 备案 + SMS 模板审核 pushed the SMS go-live out by weeks; email
verification codes are the unblock for the v0.1 launch.

`UserPublic` + `PersonaType` are intentionally re-imported from the
SMS schemas — the user representation is identical regardless of
which channel verified them.
"""

from pydantic import BaseModel, Field

from app.schemas.auth import UserPublic

# Pragmatic email-validation pattern. Keeps us off the `email-validator`
# package (would pull in `idna` + `dnspython`) — full RFC-5322 is not
# the goal; the goal is "looks like an email so a typo doesn't slip
# past the schema and reach the SMTP dispatcher". The real check is
# the verification code itself: a typoed address never sees the code.
EMAIL_PATTERN = r"^[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}$"
EMAIL_MAX_LENGTH = 254  # RFC 5321 §4.5.3.1.3 hard limit on path length


class EmailSendRequest(BaseModel):
    """Body for `POST /v1/auth/email/send`."""

    email: str = Field(
        ...,
        description="Recipient email address. Must look like an email; we do not verify the MX record.",
        pattern=EMAIL_PATTERN,
        max_length=EMAIL_MAX_LENGTH,
        examples=["alex@example.com"],
    )


class EmailSendResponse(BaseModel):
    """Same shape as `SmsSendResponse` — UI resend-cooldown hint."""

    ttl: int = Field(
        ...,
        description="Seconds until the user may request another code. Hardcoded 60s in v0.1.",
        ge=1,
        examples=[60],
    )


class EmailVerifyRequest(BaseModel):
    """Body for `POST /v1/auth/email/verify`."""

    email: str = Field(
        ...,
        pattern=EMAIL_PATTERN,
        max_length=EMAIL_MAX_LENGTH,
        examples=["alex@example.com"],
    )
    code: str = Field(
        ...,
        description="6-digit verification code received by email.",
        pattern=r"^\d{6}$",
        examples=["123456"],
    )


class EmailVerifyResponse(BaseModel):
    """Mirror of `SmsVerifyResponse` — JWT envelope is channel-agnostic."""

    token: str = Field(
        ...,
        description="JWT bearer token. Pass as `Authorization: Bearer <token>`.",
        examples=["eyJhbGciOiJIUzI1NiJ9..."],
    )
    user: UserPublic


__all__ = [
    "EMAIL_MAX_LENGTH",
    "EMAIL_PATTERN",
    "EmailSendRequest",
    "EmailSendResponse",
    "EmailVerifyRequest",
    "EmailVerifyResponse",
]
