"""Wrapped 卡分享路由 — PRD §7.9.

All three card types now resolve to real handlers backed by
`ShareCardService`. Session is the per-end card; weekly is the
Mon-Sun digest; wrapped is the annual 6-page recap.

Auth is hard: every endpoint here depends on `get_current_user_id`,
which 401s on a missing or invalid bearer token. The `anonymous`
sentinel survives only on legacy DB rows minted during the prior
soft-auth window; the dependency itself can no longer return it.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Path, Request, status

from app.middleware import get_request_id
from app.schemas.sharecards import (
    SessionShareCardRequest,
    ShareCardResponse,
    WeeklyShareCardRequest,
    WrappedShareCardRequest,
)
from app.services.auth import CurrentUser, get_current_user, get_current_user_id
from app.services.sharecards import (
    ShareCardCaptionBlockedError,
    ShareCardNotFoundError,
    ShareCardService,
    get_sharecard_service,
)

router = APIRouter(prefix="/sharecards", tags=["sharecards"])


def _trace_id(request: Request) -> str:
    return get_request_id(request)


@router.post(
    "/session/{session_id}",
    response_model=ShareCardResponse,
    summary="Render a 9:16 share card for a finished sandbox session",
    responses={
        400: {"description": "user_caption_override blocked by content moderation"},
        404: {"description": "session not found / no scorecard yet"},
    },
)
async def create_session_card(
    payload: SessionShareCardRequest,
    request: Request,
    session_id: str = Path(
        ...,
        description="Session id from POST /v1/sessions; must be in `ended` state.",
        examples=["ses_018f3a8b1c2d7e3a"],
    ),
    service: ShareCardService = Depends(get_sharecard_service),
    current: CurrentUser = Depends(get_current_user),
) -> ShareCardResponse:
    """`is_minor` flows from the JWT so the caption-override moderation
    check uses the strict tier for under-18 users (PRD §3.0.5 C).
    Weekly + wrapped don't take user content, so they only need the
    user id."""
    trace_id = _trace_id(request)
    try:
        return await service.create_session_card(
            session_id=session_id,
            request=payload,
            user_id=current.user_id,
            is_minor=current.is_minor,
            trace_id=trace_id,
        )
    except ShareCardNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={
                "code": "NOT_FOUND",
                "message": f"session {session_id} has no scorecard yet",
            },
        ) from exc
    except ShareCardCaptionBlockedError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "code": "CAPTION_BLOCKED",
                "message": (
                    "user_caption_override blocked by content moderation "
                    f"({', '.join(exc.categories) or 'unknown'})"
                ),
            },
        ) from exc


@router.post(
    "/weekly",
    response_model=ShareCardResponse,
    summary="Render the weekly digest card (cron and on-demand both call this)",
)
async def create_weekly_card(
    payload: WeeklyShareCardRequest,
    request: Request,
    service: ShareCardService = Depends(get_sharecard_service),
    user_id: str = Depends(get_current_user_id),
) -> ShareCardResponse:
    return await service.create_weekly_card(
        request=payload,
        user_id=user_id,
        trace_id=_trace_id(request),
    )


@router.post(
    "/wrapped/year/{year}",
    response_model=ShareCardResponse,
    summary="Render the 6-page annual Wrapped recap",
)
async def create_wrapped_card(
    payload: WrappedShareCardRequest,
    request: Request,
    year: int = Path(
        ...,
        ge=2024,
        le=2099,
        description="Calendar year. Bounded to keep generated PNGs cacheable.",
        examples=[2026],
    ),
    service: ShareCardService = Depends(get_sharecard_service),
    user_id: str = Depends(get_current_user_id),
) -> ShareCardResponse:
    return await service.create_wrapped_card(
        year=year,
        request=payload,
        user_id=user_id,
        trace_id=_trace_id(request),
    )
