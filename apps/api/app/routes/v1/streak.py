"""Streak endpoint — PRD §7.11.

`GET /v1/streak` returns the home screen's consecutive-practice-days
counter (StreakFire). The streak is *advanced* by `POST /v1/sessions`
(starting practice); this route is read-only.

No age gate: reading a counter touches no LLM path.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends

from app.schemas.streak import StreakResponse
from app.services.auth import CurrentUser, get_current_user
from app.services.streak import StreakService, get_streak_service

router = APIRouter(prefix="/streak", tags=["streak"])


@router.get(
    "",
    response_model=StreakResponse,
    summary="Get the caller's practice streak",
)
async def get_streak(
    service: StreakService = Depends(get_streak_service),
    current: CurrentUser = Depends(get_current_user),
) -> StreakResponse:
    """A user who has never practised gets `{current_days: 0, max_days:
    0}`, not a 404. `user_id` comes from the JWT."""
    record = await service.get_streak(current.user_id)
    return StreakResponse(current_days=record.current_days, max_days=record.max_days)
