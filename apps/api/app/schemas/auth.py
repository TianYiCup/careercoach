"""Auth / SMS endpoints (PRD §7.2)."""

from typing import Literal

from pydantic import BaseModel, Field

PersonaType = Literal["in_school", "intern", "graduate"]


class SmsSendRequest(BaseModel):
    phone: str = Field(
        ...,
        description="Mainland China mobile number, 11 digits, no country prefix.",
        pattern=r"^1[3-9]\d{9}$",
        examples=["13800138000"],
    )


class SmsSendResponse(BaseModel):
    ttl: int = Field(
        ...,
        description="Seconds until the user may request another code. Hardcoded 60s in v0.1.",
        ge=1,
        examples=[60],
    )


class SmsVerifyRequest(BaseModel):
    phone: str = Field(..., pattern=r"^1[3-9]\d{9}$", examples=["13800138000"])
    code: str = Field(
        ...,
        description="6-digit verification code received by SMS.",
        pattern=r"^\d{6}$",
        examples=["123456"],
    )


class UserPublic(BaseModel):
    """Fields safe to surface to the client. Never include `phone` raw — masked."""

    id: str = Field(..., description="UUID.", examples=["u_018f3a8b-1c2d-7e3a-b4f5-6c7d8e9f0a1b"])
    nickname: str = Field(..., examples=["小苏"])
    persona_type: PersonaType = Field(..., examples=["in_school"])
    is_minor: bool = Field(
        ...,
        description="True if user.birthdate puts them under 18 — triggers minor mode (PRD §3.0.5 C).",
        examples=[False],
    )


class SmsVerifyResponse(BaseModel):
    token: str = Field(
        ...,
        description="JWT bearer token. Pass as `Authorization: Bearer <token>`.",
        examples=["eyJhbGciOiJIUzI1NiJ9..."],
    )
    user: UserPublic
