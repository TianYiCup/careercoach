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


class CharacterVectorPayload(BaseModel):
    """6-dim opponent persona profile (Character Engine L1) returned on
    session create so the frontend can render the L9 radar chart.

    Each dim is 0-100; semantics live with
    `app.services.scenarios.character_vector.CharacterVector`. The
    frontend treats this as a display-only snapshot — future epics
    (L3 Mood Arbiter) will move these values during the conversation,
    at which point the radar animates."""

    aggression: int = Field(..., ge=0, le=100)
    empathy: int = Field(..., ge=0, le=100)
    control: int = Field(..., ge=0, le=100)
    honesty: int = Field(..., ge=0, le=100)
    stability: int = Field(..., ge=0, le=100)
    power_gap: int = Field(..., ge=0, le=100)


class SessionMemoryPayload(BaseModel):
    """The opponent's recall of this (user, scenario), if any (L6).

    Present on session create when the user has finished this scenario
    before, so the frontend can show a "对手记得你 · 第 N 次" badge.
    Null on a first visit."""

    visit_count: int = Field(
        ...,
        ge=1,
        description="How many times the user has finished this scenario before this session.",
        examples=[2],
    )
    last_result: ScoreResult = Field(
        ...,
        description="The verdict of the user's most recent session in this scenario.",
        examples=["fanche"],
    )


class CreateSessionResponse(BaseModel):
    session_id: str = Field(..., examples=["ses_018f3a8b1c2d7e3a"])
    opening_line: str = Field(
        ...,
        description="Opponent's first line, generated server-side from scenario + persona.",
        examples=["小林啊，这个周末项目得加个班，应该没问题吧？"],
    )
    character_vector: CharacterVectorPayload = Field(
        ...,
        description=(
            "Opponent's 6-dim persona profile (L1). The frontend renders "
            "this as the L9 radar chart so the user can see who they're "
            "up against at a glance."
        ),
    )
    memory: SessionMemoryPayload | None = Field(
        default=None,
        description=(
            "L6 long-term memory recall. Present when the opponent "
            "remembers the user from a past session in this scenario; "
            "null on a first visit."
        ),
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
