"""Tests for `InMemoryScenarioRepository`.

These pin the catalog shape: every seeded scenario must be retrievable
by id, the listing must round-trip immutably, and unknown ids must
fail closed (None) so the service layer can decide whether to 404.
"""

from __future__ import annotations

from app.services.scenarios.repository import InMemoryScenarioRepository
from app.services.scenarios.seed_data import SCENARIO_CATALOG


async def test_list_all_returns_every_seeded_scenario() -> None:
    repo = InMemoryScenarioRepository()

    records = await repo.list_all()

    assert len(records) == len(SCENARIO_CATALOG)
    ids = {r.id for r in records}
    assert {"sc_001", "sc_002", "sc_003"} <= ids  # MSW-compatible core


async def test_list_all_returns_fresh_list_callers_can_mutate() -> None:
    """Callers must not be able to reach into the singleton's view.

    The protocol returns a list (not a tuple) precisely so the route
    layer can sort or slice without freezing the rest of the system."""
    repo = InMemoryScenarioRepository()

    first = await repo.list_all()
    first.clear()

    second = await repo.list_all()
    assert second  # still populated after the caller emptied their copy


async def test_get_known_id_returns_record() -> None:
    repo = InMemoryScenarioRepository()

    record = await repo.get("sc_001")

    assert record is not None
    assert record.title == "周末加班谈判"
    assert record.persona_title == "强硬型 HR"


async def test_get_unknown_id_returns_none() -> None:
    """Repository fails closed on unknown ids — service decides the
    HTTP response. The session-side seed lookup uses its own
    `FALLBACK_RECORD` for the demo "any string works" flow."""
    repo = InMemoryScenarioRepository()

    assert await repo.get("sc_does_not_exist") is None


async def test_catalog_spans_all_four_categories() -> None:
    """Filter coverage relies on at least one scenario per category."""
    repo = InMemoryScenarioRepository()
    records = await repo.list_all()

    categories = {r.category for r in records}
    assert categories == {"campus", "jobhunt", "intern", "life"}
