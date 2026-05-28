"""Strategy-profile schemas — Character Engine L5."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


class StrategyStatItem(BaseModel):
    """One strategy's usage + effectiveness for the caller."""

    strategy: str = Field(
        ...,
        description="Closed-set strategy key (placate / direct / counter / ...).",
        examples=["placate"],
    )
    count: int = Field(..., ge=0, description="Times the user played this strategy.", examples=[7])
    good: int = Field(..., ge=0, examples=[2])
    mixed: int = Field(..., ge=0, examples=[2])
    poor: int = Field(..., ge=0, examples=[3])
    win_rate: float = Field(
        ...,
        ge=0.0,
        le=1.0,
        description="good / count — share of times this strategy landed.",
        examples=[0.29],
    )
    last_seen: datetime = Field(..., description="UTC timestamp this strategy was last read.")


class StrategyProfileResponse(BaseModel):
    """`GET /v1/users/me/profile` — the user's strategy profile (L5)."""

    stats: list[StrategyStatItem] = Field(
        default_factory=list,
        description="Per-strategy stats, highest-count first. Empty until the first coached turn.",
    )
    total_observations: int = Field(
        ...,
        ge=0,
        description="Sum of all strategy counts — the experience signal driving opponent intensity.",
        examples=[15],
    )
    overrelied_strategy: str | None = Field(
        default=None,
        description=(
            "The strategy the user leans on most while it keeps failing, "
            "or null if none qualifies. This is the crutch the opponent "
            "is built to punish."
        ),
        examples=["placate"],
    )
