"""Review-mode (复盘师) endpoints — PRD §7.6.

A-8 wired the POST stub with the `require_age_set` compliance gate.
A-11 (this module) replaces the 501 with the real synchronous flow:
the route hands off to `ReviewService.create_upload`, which mints an
upload id, persists a `processing` row, runs `analyze_review`, folds
the result back, and returns the canonical record.

The GET detail route lands here too. Both endpoints sit behind
`require_age_set` — text analysis is allowed for minors per PRD §1.5
(no recording red line), so there is no `require_adult` gate; the
strict moderation tier handles age-sensitive content.

Ownership pattern
-----------------
`ReviewService.get_upload` returns None for both "no such row" and
"row belongs to a different user", so the route maps both to a single
404. A separate 403 would let a probing client enumerate upload_ids
by status code.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Path, Request, status

from app.middleware import get_request_id
from app.schemas.review import (
    CreateReviewUploadRequest,
    CreateReviewUploadResponse,
    ReviewSummaryResponse,
    ReviewTurnResponse,
    ReviewUploadResponse,
)
from app.services.auth import CurrentUser, require_age_set
from app.services.review import (
    ReviewInputBlockedError,
    ReviewService,
    ReviewUploadRecord,
    get_review_service,
)

router = APIRouter(prefix="/review", tags=["review"])


@router.post(
    "/uploads",
    response_model=CreateReviewUploadResponse,
    summary="Upload a conversation transcript for review (sync analysis, text-only)",
    responses={
        400: {
            "description": (
                "`USER_INPUT_BLOCKED` — moderation flagged the uploaded text "
                "as red-line content (PRD §3.0.5). Body lists the categories."
            ),
        },
        401: {"description": "Missing or invalid bearer token."},
        403: {
            "description": "`AGE_REQUIRED` if the JWT lacks `age_set=true` (PRD §1.5).",
        },
    },
)
async def create_review_upload(
    payload: CreateReviewUploadRequest,
    request: Request,
    current: CurrentUser = Depends(require_age_set),
    service: ReviewService = Depends(get_review_service),
) -> CreateReviewUploadResponse:
    """Sync today: analyzes the transcript inside the request and
    returns `done` / `failed`. Full turns + summary live on the GET
    detail route — keeps the POST envelope identical to the A-8 lock
    so B's generated client doesn't churn.

    `current.is_minor` flows into the moderation cascade so the strict
    tier (PRD §3.0.5 C) fires for under-18 users — `warn` from the
    backend is elevated to a `block` verdict, which the route surfaces
    as USER_INPUT_BLOCKED instead of letting an edge-case message
    through to the LLM."""
    try:
        record = await service.create_upload(
            text=payload.text,
            user_id=current.user_id,
            is_minor=current.is_minor,
            trace_id=get_request_id(request),
        )
    except ReviewInputBlockedError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "code": "USER_INPUT_BLOCKED",
                "message": (
                    "review input blocked by content moderation "
                    f"({', '.join(exc.categories) or 'unknown'})"
                ),
            },
        ) from exc
    return CreateReviewUploadResponse(upload_id=record.upload_id, status=record.status)


@router.get(
    "/uploads/{upload_id}",
    response_model=ReviewUploadResponse,
    summary="Fetch the analysed review (turns + summary)",
    responses={
        401: {"description": "Missing or invalid bearer token."},
        403: {"description": "`AGE_REQUIRED` if the JWT lacks `age_set=true`."},
        404: {"description": "Upload does not exist or belongs to a different user."},
    },
)
async def get_review_upload(
    upload_id: str = Path(..., description="Upload id from POST /v1/review/uploads."),
    current: CurrentUser = Depends(require_age_set),
    service: ReviewService = Depends(get_review_service),
) -> ReviewUploadResponse:
    """Returns the canonical record. Cross-user access is silently
    folded into 404 — a probing client cannot tell "exists but not
    yours" apart from "does not exist"."""
    record = await service.get_upload(upload_id, user_id=current.user_id)
    if record is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "NOT_FOUND", "message": f"upload {upload_id} not found"},
        )
    return _record_to_response(record)


def _record_to_response(record: ReviewUploadRecord) -> ReviewUploadResponse:
    """Map persistence record → HTTP envelope.

    Summary is None unless status is `done` — even if a partial
    `summary_score` is present (it should not be), surfacing it on a
    `processing` / `failed` upload would mislead the UI. Tying the
    summary's existence to the status field keeps the contract crisp.
    """
    summary: ReviewSummaryResponse | None = None
    if record.status == "done" and record.summary_score is not None:
        summary = ReviewSummaryResponse(
            score=record.summary_score,
            top_failures=list(record.summary_top_failures),
            improvements=list(record.summary_improvements),
        )
    return ReviewUploadResponse(
        upload_id=record.upload_id,
        status=record.status,
        turns=[
            ReviewTurnResponse(
                turn_idx=turn.turn_idx,
                speaker=turn.speaker,
                content=turn.content,
                verdict=turn.verdict,
                reason=turn.reason,
                better=turn.better,
            )
            for turn in record.turns
        ],
        summary=summary,
        created_at=record.created_at,
        completed_at=record.completed_at,
    )
