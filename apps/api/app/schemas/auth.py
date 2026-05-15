"""Auth / SMS / profile endpoints (PRD §7.2 / §1.5)."""

from typing import Literal

from pydantic import BaseModel, Field

# Bound the birth year so a typo like `99999` doesn't land. A future
# year (e.g. `2099` from a misclick) is permitted by the schema; the
# `compute_is_minor` math returns True for negative ages, so a typoed
# user lands on the strict tier (`is_minor=True`) — fail-closed, which
# is exactly the property we want for a minor gate.
_MIN_BIRTH_YEAR = 1900
_MAX_BIRTH_YEAR = 2100

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


class UpdateBirthYearRequest(BaseModel):
    """Set the caller's birth year for the minor-mode gate (PRD §1.5).

    We collect year only (minimal PII) — a mid-year proxy is enough
    for the `<18` decision. Static bounds catch gross typos
    (`99999`, `0`); years beyond the current year are accepted and
    land the caller on the strict tier (`compute_is_minor` returns
    True for negative ages — fail-closed).
    """

    birth_year: int = Field(
        ...,
        ge=_MIN_BIRTH_YEAR,
        le=_MAX_BIRTH_YEAR,
        description=(
            "Year of birth as a 4-digit integer. Server derives "
            "`is_minor` from this; the client cannot self-declare it."
        ),
        examples=[2003],
    )
