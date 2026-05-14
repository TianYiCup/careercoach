"""Content moderation — PRD §7.8 / §3.0.5 red-line.

The route owns:
  * trace-id resolution (echo `x-request-id` when present)
  * `ModerationService` dependency wiring
  * JWT-derived `user_id` override for the audit row

It does NOT decide verdicts or talk to the DB; that lives in
`app.services.moderation`. Backends are swapped behind the service
without touching this file.

Auth posture
------------
Same soft auth as sessions / sharecards: a valid Bearer token gives
the real user id, anything else falls back to `"anonymous"`. The
audit row always carries the JWT-derived id — never the payload's
self-reported one — so a caller can't frame another user by stuffing
their id into the request body.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request

from app.middleware import get_request_id
from app.schemas.moderation import ModerationCheckRequest, ModerationCheckResponse
from app.services.auth import get_current_user_id
from app.services.moderation import ModerationService, get_moderation_service

router = APIRouter(prefix="/moderation", tags=["moderation"])


@router.post(
    "/check",
    response_model=ModerationCheckResponse,
    summary="Synchronous content moderation gate",
)
async def moderation_check(
    payload: ModerationCheckRequest,
    request: Request,
    service: ModerationService = Depends(get_moderation_service),
    user_id: str = Depends(get_current_user_id),
) -> ModerationCheckResponse:
    # The JWT-derived id is authoritative for audit. The schema-level
    # `payload.user_id` field is kept for v0.1 contract stability —
    # future PR drops it once frontends migrate. Override here so the
    # ModerationEvent row reflects the actual caller, not whoever the
    # client claims to be.
    trusted = payload.model_copy(update={"user_id": user_id})
    return await service.check(trusted, trace_id=get_request_id(request))
