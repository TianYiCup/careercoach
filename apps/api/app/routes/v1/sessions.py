"""Sandbox session endpoints — PRD §7.4.

PR 4a wires `POST /v1/sessions` and `POST /v1/sessions/{id}/end` to a
real `SessionService`. `/turns` stays 501 until PR 4b lands the SSE
pipe and LangGraph judge integration.

The auth boundary is still anonymous — Sprint-2 swaps `_ANONYMOUS_USER_ID`
for `Depends(get_current_user)` once SMS verify is real.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Path, status

from app.routes.v1._stub import STUB_RESPONSES, not_implemented
from app.schemas.sessions import (
    CreateSessionRequest,
    CreateSessionResponse,
    EndSessionResponse,
    TurnRequest,
)
from app.schemas.sse import SseEventEnvelope
from app.services.sessions import (
    SessionAlreadyEndedError,
    SessionNotFoundError,
    SessionService,
    get_session_service,
)

router = APIRouter(prefix="/sessions", tags=["sessions"])

_ANONYMOUS_USER_ID = "anonymous"


@router.post(
    "",
    response_model=CreateSessionResponse,
    summary="Create a new sandbox session",
)
async def create_session(
    payload: CreateSessionRequest,
    service: SessionService = Depends(get_session_service),
) -> CreateSessionResponse:
    return await service.create_session(payload, user_id=_ANONYMOUS_USER_ID)


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
        **STUB_RESPONSES,
    },
    summary="Submit a user turn and stream the opponent reply (SSE)",
)
async def post_turn(
    payload: TurnRequest,
    session_id: str = Path(..., description="Session id from POST /v1/sessions."),
) -> None:
    raise not_implemented("POST /v1/sessions/{id}/turns")


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
) -> EndSessionResponse:
    try:
        return await service.end_session(session_id, user_id=_ANONYMOUS_USER_ID)
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
