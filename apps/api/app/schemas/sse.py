"""SSE event payload schemas for POST /v1/sessions/{id}/turns (PRD §7.4).

Each model represents the JSON body of a single SSE `data:` line. The wire
format is:

    event: <event_name>
    data: <json model>

The frontend should switch on `event` to pick the matching model. We expose
all four payloads in the OpenAPI schema (under components/schemas) so
openapi-typescript generates a discriminated union for B's MSW + client.
"""

from typing import Annotated, Literal

from pydantic import BaseModel, Field


class OpponentDeltaEvent(BaseModel):
    """Streaming token chunk from the opponent persona. Fired many times per turn."""

    text: str = Field(
        ...,
        description="Next token chunk. Concatenate in order to reconstruct the full reply.",
        examples=["什么"],
    )


class OpponentDoneEvent(BaseModel):
    """Terminal event for the opponent reply. Fires exactly once per turn."""

    turn_id: str = Field(
        ...,
        description="Server-assigned turn id; use it when calling /sessions/{id}/end.",
        examples=["t_018f3a8b1c2d7e3a"],
    )
    full_text: str = Field(
        ...,
        description="Complete opponent reply, equivalent to the concatenation of all deltas.",
        examples=["什么安排比工作还重要？"],
    )


class CoachHintEvent(BaseModel):
    """Three-tier coach suggestions surfaced after the opponent finishes (design-spec §6).

    All three tiers are returned together so the UI can render the HintCardV2
    pill switcher without an extra round-trip.
    """

    safe: str = Field(
        ...,
        description="稳如老狗 🐶 — low-risk reply.",
        examples=["可以反问 deadline，让对方先暴露底牌"],
    )
    aggressive: str = Field(
        ...,
        description="正面刚 🔥 — direct push-back.",
        examples=["直接质疑加班合理性"],
    )
    humor: str = Field(
        ...,
        description="整活儿 🤡 — humor / deflection.",
        examples=["用 '离婚' 当借口"],
    )


class MetaEvent(BaseModel):
    """Quota / meter update. Fires after each turn so the UI can re-render the counter."""

    turns_used: int = Field(..., ge=0, examples=[5])
    turns_left: int = Field(..., ge=0, examples=[25])


SseEventName = Literal["opponent.delta", "opponent.done", "coach.hint", "meta"]


class _OpponentDeltaFrame(BaseModel):
    event: Literal["opponent.delta"] = "opponent.delta"
    data: OpponentDeltaEvent


class _OpponentDoneFrame(BaseModel):
    event: Literal["opponent.done"] = "opponent.done"
    data: OpponentDoneEvent


class _CoachHintFrame(BaseModel):
    event: Literal["coach.hint"] = "coach.hint"
    data: CoachHintEvent


class _MetaFrame(BaseModel):
    event: Literal["meta"] = "meta"
    data: MetaEvent


SseEventFrame = Annotated[
    _OpponentDeltaFrame | _OpponentDoneFrame | _CoachHintFrame | _MetaFrame,
    Field(
        discriminator="event",
        description=(
            "One frame of the /sessions/{id}/turns SSE stream. "
            "The `event` tag selects the matching `data` shape."
        ),
    ),
]
"""Discriminated union of all SSE frames emitted by /sessions/{id}/turns.

Used as a response_model on the route so FastAPI registers all four payload
schemas + the four frame wrappers in components/schemas. Frontend's
openapi-typescript output will produce a tagged union the UI can switch on.
"""


class SseEventEnvelope(BaseModel):
    """Wrapper that lets FastAPI register `SseEventFrame` in components/schemas."""

    frame: SseEventFrame
