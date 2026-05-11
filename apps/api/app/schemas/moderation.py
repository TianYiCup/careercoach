"""Content moderation (PRD §7.8 / §3.0.5 红线)."""

from typing import Literal

from pydantic import BaseModel, Field

ModerationContext = Literal["user_input", "ai_output", "scenario_custom"]
ModerationCategory = Literal[
    "self_harm",
    "violence",
    "loan",
    "harassment",
    "political",
    "other",
]
ModerationVerdict = Literal["allow", "warn", "redirect", "block"]


class RedirectResource(BaseModel):
    """Crisis-line / help resource surfaced when verdict == 'redirect' (PRD §3.0.5 A)."""

    title: str = Field(..., examples=["心理援助 24h 热线"])
    url: str = Field(..., examples=["tel:010-82951332"])


class ModerationCheckRequest(BaseModel):
    content: str = Field(
        ...,
        min_length=1,
        max_length=8000,
        description="Raw text to score. Caller MUST NOT pre-filter — moderation owns the decision.",
    )
    context: ModerationContext = Field(
        ...,
        description="Where the content came from. Drives different sensitivity tables.",
        examples=["user_input"],
    )
    user_id: str = Field(..., description="UUID of acting user (for ModerationEvent audit log).")
    session_id: str | None = Field(
        default=None,
        description="Optional — present when content is part of a Session/Turn.",
    )


class ModerationCheckResponse(BaseModel):
    verdict: ModerationVerdict = Field(
        ...,
        description="allow = pass / warn = soft notice / redirect = surface resource / block = stop.",
    )
    categories: list[ModerationCategory] = Field(
        default_factory=list,
        description="Categories triggered. Empty when verdict == 'allow'.",
    )
    score: float = Field(
        ...,
        ge=0.0,
        le=1.0,
        description="Confidence in [0,1]. Higher = more sure the content is unsafe.",
    )
    redirect_resource: RedirectResource | None = Field(
        default=None,
        description="Present iff verdict == 'redirect'. Null otherwise.",
    )
    trace_id: str = Field(..., description="Echoes the request trace id for log correlation.")
