"""Vibe check-in request/response schemas — PRD §7.11."""

from __future__ import annotations

from datetime import date
from typing import Literal

from pydantic import BaseModel, Field

# Mirrors `app.services.vibe.repository.VibeType`. Duplicated on purpose
# so the public schema layer stays independent of the service layer —
# same precedent as `PrivacyLevel` in `schemas/copilot.py`.
VibeType = Literal["fire", "tired", "anxious", "excited", "meh"]


class SetVibeRequest(BaseModel):
    """`POST /v1/vibe/today` body — PRD §7.11 `{ "vibe": "fire" }`."""

    vibe: VibeType = Field(
        ...,
        description="Today's mood. One of fire / tired / anxious / excited / meh.",
        examples=["fire"],
    )


class VibeResponse(BaseModel):
    """Confirms the recorded check-in. The home screen uses `vibe` to
    pick 教练 K's expression and bias scenario recommendations."""

    vibe: VibeType = Field(..., description="The mood just recorded.", examples=["fire"])
    logged_date: date = Field(
        ...,
        description="Asia/Shanghai calendar date the check-in is filed under.",
        examples=["2026-05-20"],
    )
