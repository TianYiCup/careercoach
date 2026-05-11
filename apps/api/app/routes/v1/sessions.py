"""Sandbox session endpoints — PRD §7.4."""

from fastapi import APIRouter, Path

from app.routes.v1._stub import STUB_RESPONSES, not_implemented
from app.schemas.sessions import (
    CreateSessionRequest,
    CreateSessionResponse,
    EndSessionResponse,
    TurnRequest,
)
from app.schemas.sse import SseEventEnvelope

router = APIRouter(prefix="/sessions", tags=["sessions"])


@router.post(
    "",
    response_model=CreateSessionResponse,
    responses=STUB_RESPONSES,
    summary="Create a new sandbox session",
)
async def create_session(payload: CreateSessionRequest) -> CreateSessionResponse:
    raise not_implemented("POST /v1/sessions")


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
    responses=STUB_RESPONSES,
    summary="End a session and emit the scorecard + weakness deltas",
)
async def end_session(
    session_id: str = Path(..., description="Session id from POST /v1/sessions."),
) -> EndSessionResponse:
    raise not_implemented("POST /v1/sessions/{id}/end")
