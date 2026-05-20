"""Streak response schema — PRD §7.11."""

from __future__ import annotations

from pydantic import BaseModel, Field


class StreakResponse(BaseModel):
    """`GET /v1/streak` — drives the home screen's StreakFire counter."""

    current_days: int = Field(
        ...,
        ge=0,
        description="Consecutive practice days up to and including today.",
        examples=[12],
    )
    max_days: int = Field(
        ...,
        ge=0,
        description="All-time best streak.",
        examples=[27],
    )
