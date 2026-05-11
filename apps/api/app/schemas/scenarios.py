"""Scenario library (PRD §7.3)."""

from typing import Literal

from pydantic import BaseModel, Field

ScenarioCategory = Literal["campus", "jobhunt", "intern", "life"]


class ScenarioSummary(BaseModel):
    id: str = Field(..., examples=["sc_001"])
    title: str = Field(..., examples=["周末加班谈判"])
    category: ScenarioCategory = Field(..., examples=["intern"])
    difficulty: int = Field(
        ...,
        ge=1,
        le=5,
        description="1 = easy / 5 = hard. Difficulty drives Persona aggression.",
        examples=[3],
    )
    tags: list[str] = Field(default_factory=list, examples=[["拒绝", "上下级"]])
    background: str = Field(
        ...,
        description="Short scenario blurb shown in the picker card.",
        examples=["你刚结束周五的项目，老板在群里 @ 你..."],
    )
    real_user_certified: bool = Field(
        ...,
        description="True only if ≥ 5 real students validated this scenario (PRD §3.0.5 D).",
        examples=[True],
    )


class ScenarioListResponse(BaseModel):
    items: list[ScenarioSummary]
    total: int = Field(..., ge=0, description="Total count across all pages, not just `items`.")
