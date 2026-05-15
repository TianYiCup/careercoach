"""Review-mode (复盘师) endpoints — PRD §7.6.

Stub-first for the same reason as `copilot.py`: lock the compliance
gate (`require_age_set`) and the contract shape *before* the LLM
analysis chain lands. Unlike 副驾, review is **allowed for minors** —
text analysis carries no recording / privacy red lines — so the gate
here is the age-set gate only, not `require_adult`.

v0.1 contract:
  * No token                  → 401 UNAUTHORIZED
  * Token, age_set=False      → 403 AGE_REQUIRED
  * Token, age set            → 501 NOT_IMPLEMENTED

When the real handler lands, replace `not_implemented(...)` with the
reviewer LangGraph chain. Minors will still pass the gate; the
moderation strict tier (already wired in `_apply_minor_strictness`)
handles age-sensitive content from there.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends

from app.routes.v1._stub import STUB_RESPONSES, not_implemented
from app.schemas.review import CreateReviewUploadRequest, CreateReviewUploadResponse
from app.services.auth import CurrentUser, require_age_set

router = APIRouter(prefix="/review", tags=["review"])


@router.post(
    "/uploads",
    response_model=CreateReviewUploadResponse,
    summary="Upload a conversation transcript for review (stub — text-only)",
    responses={
        **STUB_RESPONSES,
        401: {"description": "Missing or invalid bearer token."},
        403: {
            "description": "`AGE_REQUIRED` if the JWT lacks `age_set=true` (PRD §1.5).",
        },
    },
)
async def create_review_upload(
    payload: CreateReviewUploadRequest,
    current: CurrentUser = Depends(require_age_set),
) -> CreateReviewUploadResponse:
    """v0.1 only accepts JSON text bodies — image / audio multipart is v2.
    See `apps/api/app/schemas/review.py` for the rationale."""
    _ = payload  # validated via Pydantic
    _ = current  # gate consumed via Depends
    raise not_implemented("POST /v1/review/uploads")
