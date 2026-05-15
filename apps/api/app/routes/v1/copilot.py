"""Realtime copilot endpoints — PRD §7.5.

This module exists *primarily as a compliance attach point* — see
`docs/b-side-review-2026-05-15/` and the risk register entry R-15
("未成年人误用副驾产生录音合规问题"). The 副驾 flow involves voice
recording and is explicitly forbidden for minors by PRD §1.5 / §3.0.5
C and the algorithm filing commitment. By landing the route shell
*before* any ASR / WebSocket / hint pipeline arrives, the
`require_adult` gate is guaranteed to fire on day one of the eventual
business logic — there is no "we forgot to add the dependency"
failure mode possible.

v0.1 contract:
  * No token                  → 401 UNAUTHORIZED
  * Token, age_set=False      → 403 AGE_REQUIRED   (chained via require_adult → require_age_set)
  * Token, is_minor=True      → 403 MINOR_FORBIDDEN
  * Token, adult, age set     → 501 NOT_IMPLEMENTED

When the real handler lands, replace `not_implemented(...)` with the
business logic — the dependency chain stays as-is.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends

from app.routes.v1._stub import STUB_RESPONSES, not_implemented
from app.schemas.copilot import CreateCopilotSessionRequest, CreateCopilotSessionResponse
from app.services.auth import CurrentUser, require_adult

router = APIRouter(prefix="/copilot", tags=["copilot"])


@router.post(
    "/sessions",
    response_model=CreateCopilotSessionResponse,
    summary="Create a realtime copilot session (stub — adult-only gate)",
    responses={
        **STUB_RESPONSES,
        401: {"description": "Missing or invalid bearer token."},
        403: {
            "description": (
                "`AGE_REQUIRED` if the JWT lacks `age_set=true`; "
                "`MINOR_FORBIDDEN` if the caller is under 18. PRD §1.5 / R-15."
            ),
        },
    },
)
async def create_copilot_session(
    payload: CreateCopilotSessionRequest,
    current: CurrentUser = Depends(require_adult),
) -> CreateCopilotSessionResponse:
    """Validate body, run the adult-only gate, then 501 until the real
    realtime backend lands. The schema is honored so B can already
    generate the client — the 501 just signals "wired but not built"."""
    _ = payload  # validated via Pydantic; no-op in v0.1
    _ = current  # gate consumed via Depends; no-op in v0.1
    raise not_implemented("POST /v1/copilot/sessions")
