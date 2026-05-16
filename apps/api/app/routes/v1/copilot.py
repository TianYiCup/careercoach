"""Realtime copilot endpoints — PRD §7.5.

A-8 wired the POST stub with the `require_adult` compliance gate.
A-15 (this module) replaces the 501 with the real handler that mints
a copilot_id, persists a `pending` row, and returns the canonical
WebSocket URL the client connects to.

A-15 does NOT ship the WebSocket endpoint itself — that's A-17. The
`ws_url` returned here points at the future endpoint; the client can
prepare its connection state ahead of time. (Frontend must handle
"WS handshake failed" gracefully anyway for network drops, so this
is no new behavior — the URL just isn't connectable yet.)

Compliance attach point
-----------------------
`require_adult` chains through `require_age_set`, so the failure
ladder is:
  * No token                  → 401 UNAUTHORIZED
  * Token, age_set=False      → 403 AGE_REQUIRED
  * Token, is_minor=True      → 403 MINOR_FORBIDDEN
  * Token, adult, age set     → 200 (real handler runs)

R-15 in PRD §11.2 (未成年人误用副驾) is the load-bearing constraint
that makes the minor gate non-negotiable.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends

from app.schemas.copilot import CreateCopilotSessionRequest, CreateCopilotSessionResponse
from app.services.auth import CurrentUser, require_adult
from app.services.copilot import CopilotService, get_copilot_service

router = APIRouter(prefix="/copilot", tags=["copilot"])


@router.post(
    "/sessions",
    response_model=CreateCopilotSessionResponse,
    summary="Create a realtime copilot session",
    responses={
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
    service: CopilotService = Depends(get_copilot_service),
) -> CreateCopilotSessionResponse:
    """Mint a copilot session row and return the WebSocket URL.

    No LLM call, no moderation in this handler — copilot is
    adult-only (gate-enforced) and the audio + transcript moderation
    lives in the WS handler (A-17+). A-15 just persists the session
    intent so the WS endpoint can look it up on connect.
    """
    result = await service.create_session(
        scenario_hint=payload.scenario_hint,
        privacy_level=payload.privacy_level,
        user_id=current.user_id,
    )
    return CreateCopilotSessionResponse(
        copilot_id=result.record.copilot_id,
        ws_url=result.ws_url,
    )
