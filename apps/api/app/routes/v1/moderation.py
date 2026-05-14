"""Content moderation — PRD §7.8 / §3.0.5 red-line.

The route owns:
  * trace-id resolution (echo `x-request-id` when present)
  * `ModerationService` dependency wiring

It does NOT decide verdicts or talk to the DB; that lives in
`app.services.moderation`. Backends are swapped behind the service
without touching this file.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request

from app.middleware import get_request_id
from app.schemas.moderation import ModerationCheckRequest, ModerationCheckResponse
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
) -> ModerationCheckResponse:
    return await service.check(payload, trace_id=get_request_id(request))
