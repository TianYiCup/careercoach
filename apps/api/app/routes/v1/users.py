"""User profile endpoints (PRD §1.5 / §3.0.5 C).

For v0.1 this only exposes the minor-gate trigger: setting `birth_year`
so the server can derive `is_minor` and re-mint a JWT with the flag
flipped. The response intentionally matches `SmsVerifyResponse` so the
client can drop the new `{user, token}` into the same store it uses for
the SMS login flow — no special-casing.

A future PR will add full profile editing (nickname, persona_type) on
this same router.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status

from app.schemas.auth import SmsVerifyResponse, UpdateBirthYearRequest
from app.schemas.weakness import WeaknessItem, WeaknessProfileResponse
from app.services.auth import (
    AuthService,
    CurrentUser,
    ProfileUserNotFoundError,
    get_auth_service,
    get_current_user,
)
from app.services.scenarios import get_scenario_service
from app.services.scenarios.service import ScenarioService
from app.services.weakness import WeaknessService, get_weakness_service

# US-C3: how many scenarios to surface on the weakness profile — a
# short "train these next" strip, not the full catalog.
_MAX_RECOMMENDATIONS = 3

router = APIRouter(prefix="/users", tags=["users"])


@router.post(
    "/me/birth-year",
    response_model=SmsVerifyResponse,
    summary="Set the caller's birth year and re-mint JWT with refreshed minor flag",
    responses={
        404: {
            "description": (
                "JWT subject no longer corresponds to a user (deleted "
                "account or stale token after a DB wipe)."
            ),
        },
    },
)
async def update_birth_year(
    payload: UpdateBirthYearRequest,
    service: AuthService = Depends(get_auth_service),
    current: CurrentUser = Depends(get_current_user),
) -> SmsVerifyResponse:
    """Server computes `is_minor` from `birth_year` and persists. The
    response carries a fresh JWT so the client's old token (still
    carrying the previous `is_minor` value) can be discarded — no need
    to wait for the 30-day expiry to roll over."""
    try:
        return await service.update_birth_year(
            current.user_id,
            birth_year=payload.birth_year,
        )
    except ProfileUserNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={
                "code": "NOT_FOUND",
                "message": "user account not found",
            },
        ) from exc


@router.get(
    "/me/weaknesses",
    response_model=WeaknessProfileResponse,
    summary="Get the caller's weakness profile + recommended training scenarios",
)
async def get_my_weaknesses(
    weakness_service: WeaknessService = Depends(get_weakness_service),
    scenario_service: ScenarioService = Depends(get_scenario_service),
    current: CurrentUser = Depends(get_current_user),
) -> WeaknessProfileResponse:
    """弱点画像 (PRD §7.7 / US-C3). The weakness list is empty until the
    caller's first scored session; `POST /v1/sessions/{id}/end` folds
    each session's per-tag deltas in. Recommendations fall back to
    catalog scenarios — weakness-targeted matching is a follow-up.

    No age gate: reading a profile touches no LLM path. `user_id`
    comes from the JWT."""
    records = await weakness_service.get_weaknesses(current.user_id)
    catalog = await scenario_service.list_scenarios(category=None, query=None)
    return WeaknessProfileResponse(
        weaknesses=[
            WeaknessItem(tag=r.tag, frequency=r.frequency, last_seen=r.last_seen) for r in records
        ],
        recommended_scenarios=catalog.items[:_MAX_RECOMMENDATIONS],
    )
