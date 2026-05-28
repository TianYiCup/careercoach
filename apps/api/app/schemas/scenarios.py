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
    certification_count: int = Field(
        ...,
        ge=0,
        description=(
            "Number of real students who validated this scenario "
            "(PRD §3.0.5 D). The picker treats `>= 5` as certified."
        ),
        examples=[5],
    )
    real_user_certified: bool = Field(
        ...,
        description=(
            "Derived from `certification_count >= 5` (PRD §3.0.5 D). "
            "Kept on the response for backwards compatibility with "
            "existing clients that branch on the boolean."
        ),
        examples=[True],
    )


class ScenarioListResponse(BaseModel):
    items: list[ScenarioSummary]
    total: int = Field(..., ge=0, description="Total count across all pages, not just `items`.")


class CustomScenarioRequest(BaseModel):
    """`POST /v1/scenarios/custom` body — PRD §7.3 / US-A1."""

    description: str = Field(
        ...,
        min_length=6,
        max_length=1000,
        description=(
            "Free-text scenario description. ≥ 6 chars to submit, capped at 1000. "
            "PR-D4 lowered the floor from 30 — judges typing a short phrase "
            '("导师骂我怎么办") were hitting a 422 on the route.'
        ),
        examples=["我想练习向房东要求退还押金，但租房合同里关于押金的条款写得很模糊。"],
    )


class CustomScenarioResponse(BaseModel):
    """The created scenario. `scenario_id` is immediately usable as the
    `scenario_id` for `POST /v1/sessions`."""

    scenario_id: str = Field(..., examples=["sc_custom_a1b2c3d4e5f6"])
    title: str = Field(..., examples=["向房东要求退还押金"])
    background: str = Field(..., description="The practice scenario's background.")
    persona_title: str = Field(..., description="Display name of the opponent persona.")
    opening_line: str = Field(..., description="The opponent's first line of the practice.")
