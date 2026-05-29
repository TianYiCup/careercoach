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


class CoachStrategyRead(BaseModel):
    """K's read of the user's just-played turn (Character Engine L8).

    `strategy` / `upgrade` are closed-set keys (placate / concede /
    avoid / deflect / counter / reason / direct); `effect` is good /
    mixed / poor. The frontend owns the Chinese gloss per key. When the
    model goes off-vocabulary the whole object is null on the parent.
    """

    strategy: str = Field(
        ...,
        description="What tactic the user just played (closed-set key).",
        examples=["placate"],
    )
    effect: str = Field(
        ...,
        description="Whether it landed: good | mixed | poor.",
        examples=["poor"],
    )
    upgrade: str = Field(
        ...,
        description="Recommended next tactic (closed-set key). Equal to `strategy` means 保持.",
        examples=["direct"],
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
    strategy: CoachStrategyRead | None = Field(
        default=None,
        description=(
            "L8 strategy read of the user's just-played turn. Null when "
            "the model produced no parseable on-vocabulary read."
        ),
    )


class SafetySoftenEvent(BaseModel):
    """Deep emotional-safety auto-soften (Character Engine L7, minors).

    Fired at the top of a turn where accumulated harm crossed the
    threshold for an under-18 user — the opponent was force-softened for
    this reply (PRD §3.0.5 C). The frontend shows a "教练 K 介入 ·
    对手收力了" indicator. Rare by design."""

    crash_streak: int = Field(
        ...,
        ge=0,
        description="Consecutive turns the user was crushed before K stepped in.",
        examples=[3],
    )


class SafetyOfframpEvent(BaseModel):
    """Deep emotional-safety off-ramp (Character Engine L7, adults).

    Fired at the top of a turn where accumulated harm crossed the
    threshold for an adult. Unlike the minor path, the difficulty is NOT
    lowered — the opponent presses on. K just surfaces a check-in so the
    user keeps agency (push through, ease off, or step out), which keeps
    the practice real. The frontend renders a non-blocking K prompt."""

    crash_streak: int = Field(
        ...,
        ge=0,
        description="Consecutive turns the user was crushed when K checked in.",
        examples=[3],
    )


class ArcUpdateEvent(BaseModel):
    """Dramatic-arc stage for the turn (Character Engine L2).

    Fires once per turn, before `mood.update`, so the UI stage bar can
    highlight where the conversation sits (开场 → 冲突 → 转折 → 收尾).
    The same stage biases the opponent's mood via the arbiter, so the
    bar and the radar move together.
    """

    stage: Literal["opening", "conflict", "turning", "closing"] = Field(
        ...,
        description="Current dramatic-arc stage.",
        examples=["turning"],
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
    "safety.soften",
    "safety.offramp",
    "opponent.delta",
    "opponent.done",
    "arc.update",
    "mood.update",
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


class _ArcUpdateFrame(BaseModel):
    event: Literal["arc.update"] = "arc.update"
    data: ArcUpdateEvent


class _SafetySoftenFrame(BaseModel):
    event: Literal["safety.soften"] = "safety.soften"
    data: SafetySoftenEvent


class _SafetyOfframpFrame(BaseModel):
    event: Literal["safety.offramp"] = "safety.offramp"
    data: SafetyOfframpEvent


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
    | _ArcUpdateFrame
    | _SafetySoftenFrame
    | _SafetyOfframpFrame
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
