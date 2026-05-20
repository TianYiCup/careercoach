"""Daily vibe check-in endpoint — PRD §7.11.

`POST /v1/vibe/today` records how the caller feels today. The home
screen reads it back (via a later home/profile payload) to pick
教练 K's expression and bias scenario recommendations.

No age gate: a mood check-in sends nothing to an LLM, so the A-6
`require_age_set` gate (which guards LLM-bound content paths) does not
apply — a valid JWT is all that's needed.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends

from app.schemas.vibe import SetVibeRequest, VibeResponse
from app.services.auth import CurrentUser, get_current_user
from app.services.vibe import VibeService, get_vibe_service

router = APIRouter(prefix="/vibe", tags=["vibe"])


@router.post(
    "/today",
    response_model=VibeResponse,
    summary="Record the caller's mood for today",
)
async def set_today_vibe(
    payload: SetVibeRequest,
    service: VibeService = Depends(get_vibe_service),
    current: CurrentUser = Depends(get_current_user),
) -> VibeResponse:
    """One check-in per Asia/Shanghai day — a re-POST overwrites it.
    `user_id` comes from the JWT, never the request body."""
    record = await service.set_today_vibe(user_id=current.user_id, vibe=payload.vibe)
    return VibeResponse(vibe=record.vibe, logged_date=record.logged_date)
