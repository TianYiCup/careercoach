"""Review-mode (复盘师) request/response schemas — PRD §7.6.

v0.1 ships the **stub** of `POST /v1/review/uploads` with a
text-only body — image/audio multipart upload (OCR + ASR) is v2.
Reasoning: §10.1 W3 milestone demands a review demo, and the only
demo-stable path is `text` (评委粘贴对话). OCR / ASR adapters are
multi-week side quests that we explicitly砍刀 in the W3 plan.

The handler returns 501 NOT_IMPLEMENTED. The schema is locked so B
can generate types now.
"""

from __future__ import annotations

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
    """Placeholder — see copilot.CreateCopilotSessionResponse for why."""

    upload_id: str = Field(..., examples=["up_018f3a8b1c2d7e3a"])
    status: str = Field(
        ...,
        description="Processing state. v1 starts as `processing` and flips via GET.",
        examples=["processing"],
    )
