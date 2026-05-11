"""Shared response envelopes (PRD §7.1)."""

from pydantic import BaseModel, Field


class ErrorResponse(BaseModel):
    """Standard error envelope returned for any non-2xx HTTP status."""

    code: str = Field(
        ...,
        description="Stable machine-readable error code (e.g. RATE_LIMIT_EXCEEDED).",
        examples=["NOT_IMPLEMENTED"],
    )
    message: str = Field(
        ...,
        description="Human-readable, user-safe message. Never leaks internal detail.",
        examples=["this endpoint is not implemented in v0.1"],
    )
    trace_id: str = Field(
        ...,
        description="Per-request id; surface in UI so users can quote it in support.",
        examples=["a3f1c2e0-9b4d-4f1e-8c8b-1c0e7e2f6a01"],
    )
