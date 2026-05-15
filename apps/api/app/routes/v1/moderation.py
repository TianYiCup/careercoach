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
Hard auth: `get_current_user_id` returns the JWT-derived user id on a
valid Bearer token and raises 401 UNAUTHORIZED otherwise. The audit
row is always written with that JWT-derived id (passed to the service
as a separate kwarg, never via the request body) so a caller can't
frame another user.
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
    # The JWT-derived `user_id` is passed to the service as a separate
    # argument — the request schema no longer carries it, so there's
    # nothing to override. Any `user_id` a client still puts in the body
    # is silently ignored by Pydantic's `extra='ignore'` default.
    return await service.check(
        payload,
        user_id=user_id,
        trace_id=get_request_id(request),
    )
