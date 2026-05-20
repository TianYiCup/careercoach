"""Weakness-profile schemas — PRD §7.7 (弱点画像 US-C3)."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field

from app.schemas.scenarios import ScenarioSummary


class WeaknessItem(BaseModel):
    """One tracked communication weakness for the caller."""

    tag: str = Field(
        ...,
        description="Weakness tag from the product taxonomy, e.g. 过早让步.",
        examples=["过早让步"],
    )
    frequency: int = Field(
        ...,
        ge=0,
        description="Accumulated number of times this weakness has surfaced.",
        examples=[4],
    )
    last_seen: datetime = Field(
        ...,
        description="UTC timestamp the weakness was last recorded.",
    )


class WeaknessProfileResponse(BaseModel):
    """`GET /v1/users/me/weaknesses` — the 弱点画像 (US-C3)."""

    weaknesses: list[WeaknessItem] = Field(
        default_factory=list,
        description="Tracked weaknesses, highest-frequency first. Empty until first scored session.",
    )
    recommended_scenarios: list[ScenarioSummary] = Field(
        default_factory=list,
        description=(
            "Scenarios to train against. v1 surfaces catalog scenarios; "
            "weakness-targeted matching is a follow-up."
        ),
    )
