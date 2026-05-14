"""`ScenarioService` — filtering + projection for `GET /v1/scenarios`.

The repository holds raw `ScenarioRecord`s with both summary (title /
category / difficulty / tags / background / real_user_certified) and
seed (persona_title / opening_line) fields. This service is what
projects them down to the `ScenarioSummary` HTTP shape and applies
the route's two query filters:

* `category` — exact match on the literal enum
* `q` — free-text substring across title, tags, and background

`q` matches case-insensitively across Chinese + ASCII so a search for
"加班" hits sc_001 and a search for "OFFER" would hit "offer" in tags.
"""

from __future__ import annotations

import structlog

from app.schemas.scenarios import ScenarioCategory, ScenarioListResponse, ScenarioSummary
from app.services.scenarios.repository import ScenarioRepository
from app.services.scenarios.seed_data import ScenarioRecord

logger = structlog.get_logger(__name__)


class ScenarioService:
    """One method per shape — list (filtered) + get (single record)."""

    def __init__(self, *, repository: ScenarioRepository) -> None:
        self._repository = repository

    async def list_scenarios(
        self,
        *,
        category: ScenarioCategory | None,
        query: str | None,
    ) -> ScenarioListResponse:
        records = await self._repository.list_all()
        filtered = _apply_filters(records, category=category, query=query)
        items = [_to_summary(record) for record in filtered]

        logger.info(
            "scenarios_listed",
            category=category,
            query_len=len(query) if query else 0,
            returned=len(items),
            total_catalog=len(records),
        )
        return ScenarioListResponse(items=items, total=len(items))


def _apply_filters(
    records: list[ScenarioRecord],
    *,
    category: ScenarioCategory | None,
    query: str | None,
) -> list[ScenarioRecord]:
    """Sequential filter pass — category first (cheap exact match),
    then free-text (`q`). Both filters are optional and combine with
    AND semantics."""
    result = records
    if category is not None:
        result = [r for r in result if r.category == category]
    if query:
        needle = query.strip().lower()
        if needle:
            result = [r for r in result if _matches_query(r, needle)]
    return result


def _matches_query(record: ScenarioRecord, needle: str) -> bool:
    """Substring search across title, tags, and background.

    Case-insensitive lower() works on Chinese as a no-op so the same
    branch covers Chinese keyword search and ASCII tag matching.
    """
    haystacks: tuple[str, ...] = (
        record.title.lower(),
        record.background.lower(),
        *(tag.lower() for tag in record.tags),
    )
    return any(needle in haystack for haystack in haystacks)


def _to_summary(record: ScenarioRecord) -> ScenarioSummary:
    """Drop the seed-only fields (persona_title / opening_line) before
    leaving the service boundary — `/v1/scenarios` consumers shouldn't
    see what's privileged for session create."""
    return ScenarioSummary(
        id=record.id,
        title=record.title,
        category=record.category,
        difficulty=record.difficulty,
        tags=list(record.tags),
        background=record.background,
        real_user_certified=record.real_user_certified,
    )


__all__ = ["ScenarioService"]
