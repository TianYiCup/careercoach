"""Sandbox session endpoints — PRD §7.4.

PR 4a wired create + end to a real `SessionService`. PR 4b adds the
SSE-driven `/turns` endpoint backed by `TurnService`. PR 4c replaced
`/end`'s stub Score with a TurnRepository-backed aggregator. The
`/voice` endpoint (US-A3) reuses the `/turns` pipeline behind one ASR
step — see `post_voice_turn`.

Auth boundary: `get_current_user_id` is the hard dependency — every
endpoint here 401s on a missing or invalid bearer token. Legacy rows
written during the soft-auth window still carry the `anonymous`
sentinel as a *data* value, but no live request can produce one.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

from fastapi import APIRouter, Depends, File, HTTPException, Path, Request, UploadFile, status
from fastapi.responses import StreamingResponse

from app.middleware import get_request_id
from app.schemas.sessions import (
    CreateSessionRequest,
    CreateSessionResponse,
    EndSessionResponse,
    TurnRequest,
)
from app.schemas.sse import SseEventEnvelope
from app.services.auth import (
    CurrentUser,
    block_minor_quiet_hours,
    get_current_user_id,
)
from app.services.sessions import (
    SessionAlreadyEndedError,
    SessionEndedForTurnError,
    SessionNotFoundError,
    SessionNotFoundForTurnError,
    SessionService,
    TurnService,
    UserInputBlockedError,
    ValidatedTurn,
    VoiceTranscriptionError,
    VoiceTranscriptionService,
    get_session_service,
    get_turn_service,
    get_voice_transcription_service,
)
from app.services.sessions.sse import SseFrame, encode_frame
from app.services.streak import StreakService, get_streak_service
from app.services.weakness import WeaknessService, get_weakness_service

router = APIRouter(prefix="/sessions", tags=["sessions"])

# PRD §7.4 — a sandbox voice turn is one short utterance, not the
# 30-minute review recording. 8 MB comfortably covers a long 16kHz wav
# while bounding what a single `/voice` request will pull into memory.
_MAX_VOICE_UPLOAD_BYTES = 8 * 1024 * 1024


def _turn_validation_http_error(exc: Exception, *, session_id: str) -> HTTPException:
    """Map a `validate_turn_request` failure to its HTTP response.

    Shared by `POST /turns` and `POST /voice` — both run the identical
    validation and must surface the identical 404 / 409 / 400 envelope.
    """
    if isinstance(exc, SessionNotFoundForTurnError):
        return HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "NOT_FOUND", "message": f"session {session_id} not found"},
        )
    if isinstance(exc, SessionEndedForTurnError):
        return HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "ALREADY_ENDED",
                "message": f"session {session_id} has already been ended",
            },
        )
    if isinstance(exc, UserInputBlockedError):
        return HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "code": "USER_INPUT_BLOCKED",
                "message": (
                    "user input blocked by content moderation "
                    f"({', '.join(exc.categories) or 'unknown'})"
                ),
            },
        )
    raise exc  # pragma: no cover — callers only pass the three above


@router.post(
    "",
    response_model=CreateSessionResponse,
    summary="Create a new sandbox session",
)
async def create_session(
    payload: CreateSessionRequest,
    service: SessionService = Depends(get_session_service),
    streak: StreakService = Depends(get_streak_service),
    current: CurrentUser = Depends(block_minor_quiet_hours),
) -> CreateSessionResponse:
    """Starting a session means about to send user content into the
    LLM, so the compulsory age gate fires here. End-session is NOT
    gated — sessions already started must be end-able even if the
    user's age claim somehow regresses."""
    result = await service.create_session(payload, user_id=current.user_id)
    # R3-2: starting a session counts as practising today — advance the
    # streak. Best-effort: a streak-store hiccup must never fail the
    # session create.
    await streak.touch_safe(user_id=current.user_id)
    return result


@router.post(
    "/{session_id}/turns",
    responses={
        200: {
            "description": (
                "Server-Sent Events stream. Each `data:` line is a JSON "
                "`SseEventEnvelope.frame` — a discriminated union over the "
                "five event types (`opponent.delta` / `opponent.done` / "
                "`coach.hint` / `meta` / `moderation`). Frontend should "
                "switch on `event` to pick the matching `data` shape. A "
                "`moderation` frame (PRD §3.0.5 red-line interception) is "
                "the sole frame in the stream when it fires. Wire example "
                "in PRD §7.4."
            ),
            "model": SseEventEnvelope,
            "content": {"text/event-stream": {}},
        },
        400: {"description": "user content blocked by content moderation"},
        404: {"description": "session not found"},
        409: {"description": "session already ended"},
    },
    summary="Submit a user turn and stream the opponent reply (SSE)",
)
async def post_turn(
    payload: TurnRequest,
    request: Request,
    session_id: str = Path(..., description="Session id from POST /v1/sessions."),
    service: TurnService = Depends(get_turn_service),
    current: CurrentUser = Depends(block_minor_quiet_hours),
) -> StreamingResponse:
    """Validate-then-stream: typed 4xx errors come back as normal HTTP
    responses (so the client's fetch().catch() handler sees them); only
    once validation passes do we open the SSE stream.

    `current.is_minor` flows into the turn's moderation check so the
    strict tier (PRD §3.0.5 C) fires for under-18 users — `warn` from
    the backend is elevated to a `block` verdict, which the route then
    surfaces as USER_INPUT_BLOCKED instead of letting an edge-case
    message through to the LLM."""
    trace_id = get_request_id(request)
    try:
        validated = await service.validate_turn_request(
            session_id=session_id,
            content=payload.content,
            user_id=current.user_id,
            is_minor=current.is_minor,
            trace_id=trace_id,
        )
    except (
        SessionNotFoundForTurnError,
        SessionEndedForTurnError,
        UserInputBlockedError,
    ) as exc:
        raise _turn_validation_http_error(exc, session_id=session_id) from exc

    return StreamingResponse(
        _wrap_sse_stream(service.stream_turn(validated)),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",  # disable nginx buffering for SSE
        },
    )


@router.post(
    "/{session_id}/voice",
    responses={
        200: {
            "description": (
                "Server-Sent Events stream. Identical to POST /turns "
                "except the first frame is `user.transcribed` (the ASR "
                "transcript of the uploaded audio); the opponent / coach "
                "/ meta / moderation frames that follow are the same "
                "`SseEventEnvelope.frame` discriminated union. Wire "
                "example in PRD §7.4."
            ),
            "model": SseEventEnvelope,
            "content": {"text/event-stream": {}},
        },
        400: {"description": "transcribed content blocked by content moderation"},
        404: {"description": "session not found"},
        409: {"description": "session already ended"},
        413: {"description": "audio upload exceeds the size limit"},
        422: {"description": "ASR produced an empty transcript (PRD US-A3 — 没听清)"},
        502: {"description": "ASR provider unavailable — fall back to a typed turn"},
    },
    summary="Submit a voice turn — transcribe, then stream the opponent reply (SSE)",
)
async def post_voice_turn(
    request: Request,
    session_id: str = Path(..., description="Session id from POST /v1/sessions."),
    audio: UploadFile = File(..., description="16kHz wav/opus utterance (PRD §7.4)."),
    service: TurnService = Depends(get_turn_service),
    voice: VoiceTranscriptionService = Depends(get_voice_transcription_service),
    current: CurrentUser = Depends(block_minor_quiet_hours),
) -> StreamingResponse:
    """US-A3 voice turn. Transcribe the audio, then run the *identical*
    `/turns` pipeline on the transcript — the stream's first frame is
    `user.transcribed`, the rest match a typed turn.

    The audio bytes are never persisted (CLAUDE.md constraint #2 —
    不存语音); only the transcript flows on, exactly like a typed reply.
    Validate-then-stream is preserved: ASR + the shared turn validation
    surface 4xx/5xx as normal HTTP responses before the SSE stream
    opens."""
    trace_id = get_request_id(request)

    # Read at most cap+1 bytes so an oversized upload can't be pulled
    # into memory wholesale just to be rejected.
    blob = await audio.read(_MAX_VOICE_UPLOAD_BYTES + 1)
    if len(blob) > _MAX_VOICE_UPLOAD_BYTES:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail={
                "code": "AUDIO_TOO_LARGE",
                "message": f"voice upload exceeds {_MAX_VOICE_UPLOAD_BYTES} bytes",
            },
        )
    if not blob:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={"code": "EMPTY_AUDIO", "message": "the voice upload carried no audio"},
        )

    try:
        transcript = await voice.transcribe(blob)
    except VoiceTranscriptionError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail={
                "code": "ASR_UNAVAILABLE",
                "message": "voice transcription is temporarily unavailable; please type instead",
            },
        ) from exc

    if not transcript:
        # PRD US-A3 L2: 识别为空 → "没听清，请重试"，不消耗轮数 — bail
        # before any session lookup / moderation / LLM call.
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={"code": "EMPTY_TRANSCRIPTION", "message": "没听清，请重试"},
        )

    # From here it is exactly a typed turn — same validation, same 4xx
    # envelope, same SSE pipeline.
    try:
        validated = await service.validate_turn_request(
            session_id=session_id,
            content=transcript,
            user_id=current.user_id,
            is_minor=current.is_minor,
            trace_id=trace_id,
        )
    except (
        SessionNotFoundForTurnError,
        SessionEndedForTurnError,
        UserInputBlockedError,
    ) as exc:
        raise _turn_validation_http_error(exc, session_id=session_id) from exc

    return StreamingResponse(
        _wrap_sse_stream(_voice_turn_frames(transcript, validated, service)),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",  # disable nginx buffering for SSE
        },
    )


@router.post(
    "/{session_id}/end",
    response_model=EndSessionResponse,
    summary="End a session and emit the scorecard + weakness deltas",
    responses={
        404: {"description": "session not found"},
        409: {"description": "session already ended"},
    },
)
async def end_session(
    session_id: str = Path(..., description="Session id from POST /v1/sessions."),
    service: SessionService = Depends(get_session_service),
    weakness: WeaknessService = Depends(get_weakness_service),
    user_id: str = Depends(get_current_user_id),
) -> EndSessionResponse:
    try:
        result = await service.end_session(session_id, user_id=user_id)
    except SessionNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={
                "code": "NOT_FOUND",
                "message": f"session {session_id} not found",
            },
        ) from exc
    except SessionAlreadyEndedError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "ALREADY_ENDED",
                "message": f"session {session_id} has already been ended",
            },
        ) from exc

    # R3-3: fold the session's per-tag weakness deltas into the profile
    # so GET /v1/users/me/weaknesses reflects it. Best-effort — a
    # weakness-store hiccup must never fail the scorecard return.
    await weakness.apply_safe(
        user_id=user_id,
        tag_deltas={u.tag: u.delta for u in result.weakness_updates},
    )
    return result


async def _wrap_sse_stream(frames: AsyncIterator[SseFrame]) -> AsyncIterator[bytes]:
    """Render `SseFrame` objects as the on-wire byte payload SSE expects."""
    async for frame in frames:
        yield encode_frame(frame)


async def _voice_turn_frames(
    transcript: str,
    validated: ValidatedTurn,
    service: TurnService,
) -> AsyncIterator[SseFrame]:
    """Prepend the `user.transcribed` frame, then delegate to the shared
    `/turns` pipeline (PRD §7.4 — voice response = transcript + reply).

    The transcript frame is skipped on a `redirect` verdict (self-harm):
    `stream_turn` emits a lone `moderation` frame there, and that frame's
    contract is to be the *only* frame in the stream (H-1) — echoing the
    transcript first would break it.
    """
    if validated.input_verdict != "redirect":
        yield SseFrame("user.transcribed", {"text": transcript})
    async for frame in service.stream_turn(validated):
        yield frame
