"""Content moderation — PRD §7.8."""

from fastapi import APIRouter

from app.routes.v1._stub import STUB_RESPONSES, not_implemented
from app.schemas.moderation import ModerationCheckRequest, ModerationCheckResponse

router = APIRouter(prefix="/moderation", tags=["moderation"])


@router.post(
    "/check",
    response_model=ModerationCheckResponse,
    responses=STUB_RESPONSES,
    summary="Synchronous content moderation gate",
)
async def moderation_check(payload: ModerationCheckRequest) -> ModerationCheckResponse:
    raise not_implemented("POST /v1/moderation/check")
