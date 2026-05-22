"""Opponent persona library — PRD §7.3 / US-A2.

`GET /v1/personas` returns the 4 fixed base personas for the
"选择对手" picker. Read-only static catalog — no auth gate (mirrors
`GET /v1/scenarios`; touches no user data and no LLM path), and the
hidden `system_prompt` never leaves the service layer.
"""

from __future__ import annotations

from fastapi import APIRouter

from app.schemas.personas import PersonaCard, PersonaListResponse
from app.services.personas import list_personas

router = APIRouter(prefix="/personas", tags=["personas"])


@router.get(
    "",
    response_model=PersonaListResponse,
    summary="List the 4 base opponent personas",
)
async def get_personas() -> PersonaListResponse:
    """US-A2. The 4 personas are fixed (PRD §10.2) and returned easy →
    hard. `system_prompt` is dropped here — only `PersonaCard` fields
    cross the boundary."""
    items = [
        PersonaCard(
            id=record.id,
            name=record.name,
            style=record.style,
            age=record.age,
            avatar=record.avatar,
            background=record.background,
            difficulty=record.difficulty,
        )
        for record in list_personas()
    ]
    return PersonaListResponse(items=items, total=len(items))
