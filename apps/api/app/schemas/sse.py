"""SSE event payload schemas for the sandbox turn streams (PRD §7.4).

Each model represents the JSON body of a single SSE `data:` line. The wire
format is:

    event: <event_name>
    data: <json model>

The frontend should switch on `event` to pick the matching model. We expose
every payload in the OpenAPI schema (under components/schemas) so
openapi-typescript generates a discriminated union for B's MSW + client.

`POST /turns` emits `opponent.delta` / `opponent.done` / `coach.hint` /
`meta` / `moderation`. `POST /voice` prepends a `user.transcribed` frame
(the ASR transcript) and is otherwise identical — both routes share this
one frame vocabulary.
"""

from typing import Annotated, Literal

from pydantic import BaseModel, Field

from app.schemas.moderation import ModerationCategory, ModerationVerdict, RedirectResource


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


class MoodUpdateEvent(BaseModel):
    """Opponent's live 6-dim mood after the turn (Character Engine L3).

    Fires once per turn, before the opponent deltas, so the L9 radar can
    morph to the new shape as the reply streams in. Values are the same
    0-100 dims as the static `character_vector` returned on session
    create — the frontend animates from the previous mood to this one.
    """

    aggression: int = Field(..., ge=0, le=100, examples=[72])
    empathy: int = Field(..., ge=0, le=100, examples=[28])
    control: int = Field(..., ge=0, le=100, examples=[80])
    honesty: int = Field(..., ge=0, le=100, examples=[45])
    stability: int = Field(..., ge=0, le=100, examples=[65])
    power_gap: int = Field(..., ge=0, le=100, examples=[70])


class MetaEvent(BaseModel):
    """Quota / meter update. Fires after each turn so the UI can re-render the counter."""

    turns_used: int = Field(..., ge=0, examples=[5])
    turns_left: int = Field(..., ge=0, examples=[25])


class ModerationInterceptEvent(BaseModel):
    """Red-line interception frame (PRD §3.0.5).

    Emitted *instead of* a roleplay turn when the user input trips a
    `redirect` verdict — e.g. self-harm content force-interrupts the
    practice (§3.0.5 A) and surfaces a crisis-line resource. When this
    frame fires it is the ONLY frame in the stream: no opponent /
    coach / meta frames follow, and no turn is persisted.
    """

    verdict: ModerationVerdict = Field(
        ...,
        description="Moderation verdict on the user input. `redirect` for crisis content.",
        examples=["redirect"],
    )
    categories: list[ModerationCategory] = Field(
        default_factory=list,
        description="Moderation categories the input tripped.",
        examples=[["self_harm"]],
    )
    redirect_resource: RedirectResource | None = Field(
        default=None,
        description="Crisis-line / help resource. Present when verdict == 'redirect'.",
    )


class UserTranscribedEvent(BaseModel):
    """ASR transcript of an uploaded voice turn (PRD §7.4).

    The first frame of the `POST /sessions/{id}/voice` stream — emitted
    before any opponent reply so the UI can show the recognised text
    (US-A3: "1s 内屏幕显示 ASR 文本"). Never appears in a `POST /turns`
    stream, where the user already supplied the text.
    """

    text: str = Field(
        ...,
        description="Final ASR transcript of the uploaded audio.",
        examples=["赵总，我周末有重要安排"],
    )


SseEventName = Literal[
    "user.transcribed",
    "mood.update",
    "opponent.delta",
    "opponent.done",
    "coach.hint",
    "meta",
    "moderation",
]


class _OpponentDeltaFrame(BaseModel):
    event: Literal["opponent.delta"] = "opponent.delta"
    data: OpponentDeltaEvent


class _OpponentDoneFrame(BaseModel):
    event: Literal["opponent.done"] = "opponent.done"
    data: OpponentDoneEvent


class _CoachHintFrame(BaseModel):
    event: Literal["coach.hint"] = "coach.hint"
    data: CoachHintEvent


class _MoodUpdateFrame(BaseModel):
    event: Literal["mood.update"] = "mood.update"
    data: MoodUpdateEvent


class _MetaFrame(BaseModel):
    event: Literal["meta"] = "meta"
    data: MetaEvent


class _ModerationFrame(BaseModel):
    event: Literal["moderation"] = "moderation"
    data: ModerationInterceptEvent


class _UserTranscribedFrame(BaseModel):
    event: Literal["user.transcribed"] = "user.transcribed"
    data: UserTranscribedEvent


SseEventFrame = Annotated[
    _UserTranscribedFrame
    | _MoodUpdateFrame
    | _OpponentDeltaFrame
    | _OpponentDoneFrame
    | _CoachHintFrame
    | _MetaFrame
    | _ModerationFrame,
    Field(
        discriminator="event",
        description=(
            "One frame of a sandbox turn SSE stream (/turns or /voice). "
            "The `event` tag selects the matching `data` shape."
        ),
    ),
]
"""Discriminated union of every SSE frame the sandbox turn streams emit.

Used as a response_model on both /turns and /voice so FastAPI registers
all payload schemas + frame wrappers in components/schemas. Frontend's
openapi-typescript output will produce a tagged union the UI can switch
on. `user.transcribed` only ever appears in the /voice stream.
"""


class SseEventEnvelope(BaseModel):
    """Wrapper that lets FastAPI register `SseEventFrame` in components/schemas."""

    frame: SseEventFrame
