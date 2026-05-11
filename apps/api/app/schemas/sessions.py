"""Sandbox session lifecycle (PRD §7.4)."""

from typing import Literal

from pydantic import BaseModel, Field

SessionMode = Literal["sandbox", "copilot", "review"]
ScoreResult = Literal["shenfeng", "guolu", "fanche"]


class CreateSessionRequest(BaseModel):
    mode: SessionMode = Field(..., description="v0.1 only ships sandbox.", examples=["sandbox"])
    scenario_id: str = Field(..., examples=["sc_001"])
    persona_id: str = Field(..., examples=["p_hard"])
    user_goal: str = Field(
        ...,
        max_length=200,
        description="What the user wants to achieve in this practice round.",
        examples=["保住周末，不得罪老板"],
    )


class CreateSessionResponse(BaseModel):
    session_id: str = Field(..., examples=["ses_018f3a8b1c2d7e3a"])
    opening_line: str = Field(
        ...,
        description="Opponent's first line, generated server-side from scenario + persona.",
        examples=["小林啊，这个周末项目得加个班，应该没问题吧？"],
    )


class TurnRequest(BaseModel):
    """Body for POST /v1/sessions/{id}/turns. Response is SSE — see route description."""

    content: str = Field(
        ...,
        min_length=1,
        max_length=2000,
        description="User's reply text. Server runs moderation BEFORE forwarding to LLM.",
        examples=["赵总，我周末有重要安排"],
    )


class WeaknessUpdate(BaseModel):
    tag: str = Field(..., examples=["过早让步"])
    delta: int = Field(
        ...,
        description="Frequency delta from this session. Negative = improved.",
        examples=[1],
    )


class Score(BaseModel):
    """Per-session scorecard, returned on POST /v1/sessions/{id}/end."""

    aura: int = Field(..., ge=0, le=10, description="气场 / presence.")
    logic: int = Field(..., ge=0, le=10)
    emotion: int = Field(..., ge=0, le=10, description="Emotional regulation.")
    professionalism: int = Field(..., ge=0, le=10)
    goal_achieve: int = Field(..., ge=0, le=10, description="How close user got to user_goal.")
    highlights: str = Field(..., description="K's praise — what the user nailed.")
    failures: str = Field(..., description="K's call-outs — where the user slipped.")
    result: ScoreResult = Field(
        ...,
        description="封神 ≥ 8 / 路过 5–7 / 翻车 < 5. Drives Mascot expression and ShareCard gradient.",
        examples=["guolu"],
    )


class EndSessionResponse(BaseModel):
    score: Score
    weakness_updates: list[WeaknessUpdate] = Field(default_factory=list)
