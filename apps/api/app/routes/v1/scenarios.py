"""Scenario library — PRD §7.3."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query

from app.schemas.scenarios import ScenarioCategory, ScenarioListResponse
from app.services.scenarios import ScenarioService, get_scenario_service

router = APIRouter(prefix="/scenarios", tags=["scenarios"])


@router.get(
    "",
    response_model=ScenarioListResponse,
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
    service: ScenarioService = Depends(get_scenario_service),
) -> ScenarioListResponse:
    return await service.list_scenarios(category=category, query=q)
