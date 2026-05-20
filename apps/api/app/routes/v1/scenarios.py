"""Scenario library — PRD §7.3."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status

from app.middleware import get_request_id
from app.schemas.scenarios import (
    CustomScenarioRequest,
    CustomScenarioResponse,
    ScenarioCategory,
    ScenarioListResponse,
)
from app.services.auth import CurrentUser, get_current_user
from app.services.scenarios import (
    CustomScenarioBlockedError,
    CustomScenarioRateLimitedError,
    CustomScenarioService,
    ScenarioService,
    get_custom_scenario_service,
    get_scenario_service,
)

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


@router.post(
    "/custom",
    response_model=CustomScenarioResponse,
    summary="Generate a practisable scenario from a free-text description",
    responses={
        400: {"description": "description blocked by content moderation (§3.0.5 D)"},
        429: {"description": "daily custom-scenario quota exceeded (PRD §7.12)"},
    },
)
async def create_custom_scenario(
    payload: CustomScenarioRequest,
    request: Request,
    service: CustomScenarioService = Depends(get_custom_scenario_service),
    current: CurrentUser = Depends(get_current_user),
) -> CustomScenarioResponse:
    """US-A1. The returned `scenario_id` is immediately usable as the
    `scenario_id` for `POST /v1/sessions`. v1 emits a templated
    placeholder script; LLM generation lands in a follow-up.

    The description is moderated (§3.0.5 D — 自定义场景必过审): a `block`
    or `redirect` verdict rejects it with a 400."""
    trace_id = get_request_id(request)
    try:
        record = await service.create_custom_scenario(
            description=payload.description,
            user_id=current.user_id,
            is_minor=current.is_minor,
            trace_id=trace_id,
        )
    except CustomScenarioRateLimitedError as exc:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail={
                "code": "RATE_LIMIT_EXCEEDED",
                "message": f"daily limit of {exc.limit} custom scenarios reached",
            },
        ) from exc
    except CustomScenarioBlockedError as exc:
        # Distinct code from a session turn's USER_INPUT_BLOCKED so the
        # client can surface "this scenario can't be created" rather
        # than a chat-level block. A `redirect` verdict (self-harm)
        # carries the crisis resource through, like the H-1 path.
        detail: dict[str, object] = {
            "code": "SCENARIO_BLOCKED",
            "message": (
                "scenario description blocked by content moderation "
                f"({', '.join(exc.categories) or 'unknown'})"
            ),
        }
        if exc.redirect_resource is not None:
            detail["redirect_resource"] = exc.redirect_resource.model_dump()
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=detail,
        ) from exc

    return CustomScenarioResponse(
        scenario_id=record.id,
        title=record.title,
        background=record.background,
        persona_title=record.persona_title,
        opening_line=record.opening_line,
    )
