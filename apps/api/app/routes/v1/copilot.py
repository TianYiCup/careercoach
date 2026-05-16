"""Realtime copilot endpoints — PRD §7.5.

A-8 wired the POST stub with the `require_adult` compliance gate.
A-15 replaced the 501 with the real handler that mints a copilot_id,
persists a `pending` row, and returns the canonical WebSocket URL the
client connects to.
A-16 (this module) ships the WS endpoint at that URL with an echo-loop
scaffold so the round-trip handshake / persistence transitions are
locked in before ASR + real LLM hint streaming land.

Compliance attach point (POST)
------------------------------
`require_adult` chains through `require_age_set`, so the failure
ladder is:
  * No token                  → 401 UNAUTHORIZED
  * Token, age_set=False      → 403 AGE_REQUIRED
  * Token, is_minor=True      → 403 MINOR_FORBIDDEN
  * Token, adult, age set     → 200 (real handler runs)

R-15 in PRD §11.2 (未成年人误用副驾) is the load-bearing constraint
that makes the minor gate non-negotiable.

WebSocket auth model (transitional, A-16)
-----------------------------------------
The WS endpoint does NOT re-run `require_adult`. The capability ticket
is the freshly-minted `copilot_id` itself: only a `pending` row can
connect, and `pending` rows can only exist if the POST cleared the
adult gate. A future PR will tighten this with a JWT-bearing
subprotocol once B's client is comfortable signing the WS upgrade.
The 64 bits of entropy on `copilot_id` (16 hex chars) keeps brute-force
mining of valid ids out of reach for v0.
"""

from __future__ import annotations

import structlog
from fastapi import APIRouter, Depends, WebSocket
from starlette.websockets import WebSocketDisconnect

from app.schemas.copilot import CreateCopilotSessionRequest, CreateCopilotSessionResponse
from app.services.auth import CurrentUser, require_adult
from app.services.copilot import CopilotService, get_copilot_service
from app.services.copilot.service import (
    CopilotSessionNotFound,
    CopilotSessionUnavailable,
)

logger = structlog.get_logger(__name__)

router = APIRouter(prefix="/copilot", tags=["copilot"])

# Application-defined close codes (RFC 6455 §7.4.2 reserves 4000–4999
# for app use). 4404 / 4409 mirror the HTTP status semantics so any
# client that already understands "404 / 409 = retry policy" can map
# them mechanically without a per-feature lookup table.
_WS_UNKNOWN_COPILOT_CODE = 4404
_WS_SESSION_ALREADY_USED_CODE = 4409


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
    will live in the WS handler once ASR lands. A-15 just persists
    the session intent so the WS endpoint can look it up on connect.
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


@router.websocket("/sessions/{copilot_id}/stream")
async def copilot_stream(
    websocket: WebSocket,
    copilot_id: str,
    service: CopilotService = Depends(get_copilot_service),
) -> None:
    """Realtime copilot stream — A-16 scaffold (echo loop).

    Lifecycle
    ---------
    1. Accept the WS upgrade unconditionally — we always accept first
       so failures send a structured close frame instead of the opaque
       `403` that pre-accept rejection would yield. Clients then know
       how to map our 4xxx codes.
    2. `connect_session` flips the row to `connected` (or raises one
       of the two error types below).
    3. Echo loop: each text frame round-trips as
       `{"type": "echo", "text": <whatever>}`. Future PRs add new
       envelope `type`s (asr_partial, hint, score, …) without
       breaking this contract.
    4. Client disconnect (or any exception) runs `end_session` in
       `finally` — flips the row to `ended` and stamps `ended_at`.

    The `connected` flag below is the gating signal for the cleanup:
    we only call `end_session` if `connect_session` actually succeeded.
    Otherwise an "unknown id" probe would prematurely end somebody
    else's session (or a no-op write that clutters the audit log).
    """
    await websocket.accept()
    connected = False
    try:
        await service.connect_session(copilot_id)
        connected = True
    except CopilotSessionNotFound:
        await websocket.close(
            code=_WS_UNKNOWN_COPILOT_CODE,
            reason="UNKNOWN_COPILOT",
        )
        return
    except CopilotSessionUnavailable:
        await websocket.close(
            code=_WS_SESSION_ALREADY_USED_CODE,
            reason="SESSION_ALREADY_USED",
        )
        return

    try:
        while True:
            text = await websocket.receive_text()
            await websocket.send_json({"type": "echo", "text": text})
    except WebSocketDisconnect:
        # Normal client-initiated close — no need to bubble.
        logger.info("copilot_ws_disconnected", copilot_id=copilot_id)
    finally:
        if connected:
            await service.end_session(copilot_id)
