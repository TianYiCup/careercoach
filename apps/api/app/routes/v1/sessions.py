"""Sandbox session endpoints — PRD §7.4.

PR 4a wired create + end to a real `SessionService`. PR 4b adds the
SSE-driven `/turns` endpoint backed by `TurnService`. PR 4c replaced
`/end`'s stub Score with a TurnRepository-backed aggregator.

Auth boundary: `get_current_user_id` is the hard dependency — every
endpoint here 401s on a missing or invalid bearer token. Legacy rows
written during the soft-auth window still carry the `anonymous`
sentinel as a *data* value, but no live request can produce one.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

from fastapi import APIRouter, Depends, HTTPException, Path, Request, status
from fastapi.responses import StreamingResponse

from app.middleware import get_request_id
from app.schemas.sessions import (
    CreateSessionRequest,
    CreateSessionResponse,
    EndSessionResponse,
    TurnRequest,
)
from app.schemas.sse import SseEventEnvelope
from app.services.auth import CurrentUser, get_current_user, get_current_user_id
from app.services.sessions import (
    SessionAlreadyEndedError,
    SessionEndedForTurnError,
    SessionNotFoundError,
    SessionNotFoundForTurnError,
    SessionService,
    TurnService,
    UserInputBlockedError,
    get_session_service,
    get_turn_service,
)
from app.services.sessions.sse import SseFrame, encode_frame

router = APIRouter(prefix="/sessions", tags=["sessions"])


@router.post(
    "",
    response_model=CreateSessionResponse,
    summary="Create a new sandbox session",
)
async def create_session(
    payload: CreateSessionRequest,
    service: SessionService = Depends(get_session_service),
    user_id: str = Depends(get_current_user_id),
) -> CreateSessionResponse:
    return await service.create_session(payload, user_id=user_id)


@router.post(
    "/{session_id}/turns",
    responses={
        200: {
            "description": (
                "Server-Sent Events stream. Each `data:` line is a JSON "
                "`SseEventEnvelope.frame` — a discriminated union over the "
                "four event types (`opponent.delta` / `opponent.done` / "
                "`coach.hint` / `meta`). Frontend should switch on `event` "
                "to pick the matching `data` shape. Wire example in PRD §7.4."
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
    current: CurrentUser = Depends(get_current_user),
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
    except SessionNotFoundForTurnError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "NOT_FOUND", "message": f"session {session_id} not found"},
        ) from exc
    except SessionEndedForTurnError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "ALREADY_ENDED",
                "message": f"session {session_id} has already been ended",
            },
        ) from exc
    except UserInputBlockedError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "code": "USER_INPUT_BLOCKED",
                "message": (
                    "user input blocked by content moderation "
                    f"({', '.join(exc.categories) or 'unknown'})"
                ),
            },
        ) from exc

    return StreamingResponse(
        _wrap_sse_stream(service.stream_turn(validated)),
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
    user_id: str = Depends(get_current_user_id),
) -> EndSessionResponse:
    try:
        return await service.end_session(session_id, user_id=user_id)
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


async def _wrap_sse_stream(frames: AsyncIterator[SseFrame]) -> AsyncIterator[bytes]:
    """Render `SseFrame` objects as the on-wire byte payload SSE expects."""
    async for frame in frames:
        yield encode_frame(frame)
