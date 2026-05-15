"""Realtime copilot request/response schemas — PRD §7.5.

v0.1 ships the **stub** of `POST /v1/copilot/sessions`:
the request schema is locked so B can generate types, but the route
itself returns 501 NOT_IMPLEMENTED. The handler exists so the
adult-only compliance gate (`require_adult`) gets a real attach point
*before* any business logic lands — see `docs/b-side-review-2026-05-15/`
for the rationale (R-15 in PRD §11.2 demands the gate exist the moment
a copilot path is reachable).
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

PrivacyLevel = Literal["standard", "high"]


class CreateCopilotSessionRequest(BaseModel):
    """Mirrors PRD §7.5 — `{ "scenario_hint": "...", "privacy_level": "..." }`.

    `privacy_level=high` is the future on-device-ASR + redaction path
    (US-B3). v0.1 only validates the shape; the handler returns 501.
    """

    scenario_hint: str = Field(
        ...,
        min_length=1,
        max_length=200,
        description="Free-text scenario hint, e.g. '面试谈薪'.",
        examples=["面试谈薪"],
    )
    privacy_level: PrivacyLevel = Field(
        "standard",
        description=(
            "`high` enables on-device ASR + redaction (US-B3, v2). "
            "v0.1 accepts the field but does not act on it."
        ),
        examples=["high"],
    )


class CreateCopilotSessionResponse(BaseModel):
    """Placeholder so OpenAPI's 200 row points at a real schema. The
    v0.1 stub never returns 200 — the handler raises 501 — but the
    type is locked here so B's codegen can already stub a client."""

    copilot_id: str = Field(..., examples=["cop_018f3a8b1c2d7e3a"])
    ws_url: str = Field(
        ...,
        description=(
            "WebSocket URL the client connects to for audio chunks + hints. "
            "v0.1 placeholder — v1 will use SSE over the same scheme."
        ),
        examples=["wss://realtime.careercoach.ai/copilot/cop_018f3a8b1c2d7e3a"],
    )
