"""Mascot expression-timeline schemas (PRD §7.10)."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

# Mirrors `app.services.mascot.repository.MascotExpression`. Duplicated
# on purpose so the public schema layer stays independent of the
# service layer — same precedent as `VibeType` in `schemas/vibe.py`.
MascotExpression = Literal[
    "confident",
    "burning",
    "thinking",
    "shenfeng",
    "fanche",
    "integrate",
    "caring",
    "sleeping",
]


class LogMascotMomentRequest(BaseModel):
    """`POST /v1/mascot/log` body — PRD §7.10.

    The client posts one 教练 K expression switch. `session_id` ties it
    to a sandbox session; the owning `user_id` is taken from the JWT,
    never the body.
    """

    session_id: str = Field(
        ...,
        min_length=1,
        max_length=64,
        description="Session the expression switch happened in.",
        examples=["ses_018f3a8b1c2d7e3a"],
    )
    turn_idx: int = Field(
        ...,
        ge=0,
        description="Zero-based turn index the expression switched at.",
        examples=[3],
    )
    expression: MascotExpression = Field(
        ...,
        description="教练 K's new expression.",
        examples=["thinking"],
    )


class MascotMoment(BaseModel):
    """One entry in 教练 K's expression timeline."""

    turn_idx: int = Field(..., ge=0, examples=[3])
    expression: MascotExpression = Field(..., examples=["thinking"])
    at: datetime = Field(
        ...,
        description="UTC timestamp the moment was recorded (CLAUDE.md §6).",
    )


class MascotExpressionTimelineResponse(BaseModel):
    """`GET /v1/mascot/expression` — a session's expression timeline,
    oldest first. Used to replay 教练 K's reactions on the Wrapped card."""

    items: list[MascotMoment]
    total: int = Field(..., ge=0, description="Count of moments in `items`.")
