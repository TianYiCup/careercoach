"""教练 K Mascot state endpoints — PRD §7.10.

`POST /v1/mascot/log` records one expression switch; `GET
/v1/mascot/expression` reads a session's timeline back for Wrapped
replay.

No age gate: a mascot moment is a UI-state breadcrumb that touches no
LLM path, so — like `/vibe` — a valid JWT is all that's required. The
`user_id` is always taken from the JWT, which keys each timeline to its
owner: a caller can only ever read or write its own moments.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query

from app.schemas.mascot import (
    LogMascotMomentRequest,
    MascotExpressionTimelineResponse,
    MascotMoment,
)
from app.services.auth import CurrentUser, get_current_user
from app.services.mascot import MascotService, get_mascot_service

router = APIRouter(prefix="/mascot", tags=["mascot"])


@router.post(
    "/log",
    response_model=MascotMoment,
    summary="Record one 教练 K expression switch",
)
async def log_mascot_moment(
    payload: LogMascotMomentRequest,
    service: MascotService = Depends(get_mascot_service),
    current: CurrentUser = Depends(get_current_user),
) -> MascotMoment:
    """PRD §7.10 — a best-effort ("弱关联，可丢失") timeline write. The
    `at` timestamp is stamped server-side; `user_id` comes from the
    JWT."""
    record = await service.log_moment(
        user_id=current.user_id,
        session_id=payload.session_id,
        turn_idx=payload.turn_idx,
        expression=payload.expression,
    )
    return MascotMoment(
        turn_idx=record.turn_idx,
        expression=record.expression,
        at=record.at,
    )


@router.get(
    "/expression",
    response_model=MascotExpressionTimelineResponse,
    summary="Get a session's 教练 K expression timeline",
)
async def get_mascot_expression(
    session_id: str = Query(
        ...,
        min_length=1,
        max_length=64,
        description="Session id from POST /v1/sessions.",
    ),
    service: MascotService = Depends(get_mascot_service),
    current: CurrentUser = Depends(get_current_user),
) -> MascotExpressionTimelineResponse:
    """The caller's expression timeline for `session_id`, oldest first.
    A session with no logged moments returns an empty timeline, not a
    404 — the same convention `GET /v1/streak` uses for a fresh user."""
    moments = await service.get_timeline(
        user_id=current.user_id,
        session_id=session_id,
    )
    items = [MascotMoment(turn_idx=m.turn_idx, expression=m.expression, at=m.at) for m in moments]
    return MascotExpressionTimelineResponse(items=items, total=len(items))
