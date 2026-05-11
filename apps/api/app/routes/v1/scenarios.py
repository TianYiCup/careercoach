"""Scenario library — PRD §7.3."""

from fastapi import APIRouter, Query

from app.routes.v1._stub import STUB_RESPONSES, not_implemented
from app.schemas.scenarios import ScenarioCategory, ScenarioListResponse

router = APIRouter(prefix="/scenarios", tags=["scenarios"])


@router.get(
    "",
    response_model=ScenarioListResponse,
    responses=STUB_RESPONSES,
    summary="List scenarios filtered by category and free-text query",
)
async def list_scenarios(
    category: ScenarioCategory | None = Query(
        default=None,
        description="Optional category filter. Omit to get the homepage default mix.",
    ),
    q: str | None = Query(
        default=None,
        max_length=64,
        description="Free-text search across title, tags, and background.",
    ),
) -> ScenarioListResponse:
    raise not_implemented("GET /v1/scenarios")
