"""Review-mode (复盘师) request/response schemas — PRD §7.6.

A-11 makes the POST handler real and adds the GET detail route. The
POST request body is unchanged from A-8 so B's already-generated
client keeps working; the response of POST also keeps the same
`upload_id + status` envelope, with status now reflecting the
synchronous analysis outcome (`done` / `failed`). The full record
(turns + summary) lives on `GET /v1/review/uploads/{upload_id}`.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

MAX_REVIEW_TEXT_LENGTH = 5000


class CreateReviewUploadRequest(BaseModel):
    """Text-only review upload — PRD §3.3 US-C1 L4 (5000 字 ≤ 2s).

    v0.1 intentionally has no `images` / `audio` fields. Adding those
    on the v2 multipart path is a separate route operation, not an
    extension of this body — keeping JSON-only here lets B keep the
    `apiClient.post()` codepath instead of branching to FormData.
    """

    text: str = Field(
        ...,
        min_length=1,
        max_length=MAX_REVIEW_TEXT_LENGTH,
        description=(
            "Conversation transcript pasted as text. PRD §3.3 caps at "
            f"{MAX_REVIEW_TEXT_LENGTH} chars; longer payloads are 422."
        ),
        examples=["对方：你周末有空吗？\n我：有事，下次约。"],
    )


class CreateReviewUploadResponse(BaseModel):
    """POST response — minimal envelope; full record lives on the GET route.

    `status` reflects the synchronous analysis outcome (v0 today blocks
    on the LLM call). When the queue handoff lands (A-13+), this will
    return `processing` immediately and the client polls the GET route.
    The shape stays identical so B's client doesn't churn.
    """

    upload_id: str = Field(..., examples=["up_018f3a8b1c2d7e3a"])
    status: Literal["processing", "done", "failed"] = Field(
        ...,
        description=(
            "Analysis state. Today's sync flow returns `done` or `failed` "
            "directly. `processing` is reserved for the future async path."
        ),
        examples=["done"],
    )


class ReviewTurnResponse(BaseModel):
    """One analysed turn — `reason` + `better` only on user `lose` turns
    per PRD §3.3 US-C2."""

    turn_idx: int = Field(..., ge=0, examples=[0])
    speaker: Literal["user", "opponent"] = Field(..., examples=["user"])
    content: str = Field(..., examples=["有事，下次约。"])
    verdict: Literal["win", "neutral", "lose"] = Field(..., examples=["lose"])
    reason: str | None = Field(
        default=None,
        description="Why this user turn lost — populated on `lose` user turns only.",
        examples=["语气过冷，没有缓冲"],
    )
    better: str | None = Field(
        default=None,
        description="Suggested rewrite — populated on `lose` user turns only.",
        examples=["这周末确实排满了，下周二约可以吗？"],
    )


class ReviewSummaryResponse(BaseModel):
    """Whole-conversation summary. Null while `status == "processing"` and
    on `failed` uploads."""

    score: float = Field(..., ge=0, le=10, examples=[6.4])
    top_failures: list[str] = Field(
        ...,
        description="At most 3 failure points distilled from the conversation.",
        examples=[["语气过冷", "未追问"]],
    )
    improvements: list[str] = Field(
        ...,
        description="At most 3 actionable next-time-do-this suggestions.",
        examples=[["先认可再拒绝", "给一个替代时间"]],
    )


class ReviewUploadResponse(BaseModel):
    """`GET /v1/review/uploads/{upload_id}` payload — full analysed record.

    `summary` is `null` when the upload is still processing OR the
    analysis failed. Frontend should always render the turn list (which
    is present even on failure as the parsed input lines, with all
    verdicts defaulting to neutral) and only render the summary card
    when `summary` is non-null.
    """

    upload_id: str = Field(..., examples=["up_018f3a8b1c2d7e3a"])
    status: Literal["processing", "done", "failed"] = Field(..., examples=["done"])
    turns: list[ReviewTurnResponse] = Field(
        default_factory=list,
        description="Empty until analysis runs; on `failed` may also be empty.",
    )
    summary: ReviewSummaryResponse | None = Field(
        default=None,
        description="Populated on `status == 'done'`; null otherwise.",
    )
    created_at: datetime = Field(..., examples=["2026-05-16T10:00:00Z"])
    completed_at: datetime | None = Field(
        default=None,
        description="UTC timestamp when analysis finished; null while processing.",
        examples=["2026-05-16T10:00:02Z"],
    )
