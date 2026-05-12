"""Wrapped 卡分享路由 — PRD §7.9.

PR ① ships the contract: schemas validate, OpenAPI is locked, every
handler still raises `501 NOT_IMPLEMENTED` so the frontend can code-
gen and MSW can mock against the real shape. PR ② wires the Pillow
renderer; PR ③ adds DB + storage + flips the stubs to 200.
"""

from __future__ import annotations

from fastapi import APIRouter, Path

from app.routes.v1._stub import STUB_RESPONSES, not_implemented
from app.schemas.sharecards import (
    SessionShareCardRequest,
    ShareCardResponse,
    WeeklyShareCardRequest,
    WrappedShareCardRequest,
)

router = APIRouter(prefix="/sharecards", tags=["sharecards"])


@router.post(
    "/session/{session_id}",
    response_model=ShareCardResponse,
    responses=STUB_RESPONSES,
    summary="Render a 9:16 share card for a finished sandbox session",
)
async def create_session_card(
    payload: SessionShareCardRequest,
    session_id: str = Path(
        ...,
        description="Session id from POST /v1/sessions; must be in `ended` state.",
        examples=["ses_018f3a8b1c2d7e3a"],
    ),
) -> ShareCardResponse:
    raise not_implemented("POST /v1/sharecards/session/{session_id}")


@router.post(
    "/weekly",
    response_model=ShareCardResponse,
    responses=STUB_RESPONSES,
    summary="Render the weekly digest card (cron and on-demand both call this)",
)
async def create_weekly_card(payload: WeeklyShareCardRequest) -> ShareCardResponse:
    raise not_implemented("POST /v1/sharecards/weekly")


@router.post(
    "/wrapped/year/{year}",
    response_model=ShareCardResponse,
    responses=STUB_RESPONSES,
    summary="Render the 6-page annual Wrapped recap",
)
async def create_wrapped_card(
    payload: WrappedShareCardRequest,
    year: int = Path(
        ...,
        ge=2024,
        le=2099,
        description="Calendar year. Bounded to keep generated PNGs cacheable.",
        examples=[2026],
    ),
) -> ShareCardResponse:
    raise not_implemented("POST /v1/sharecards/wrapped/year/{year}")
