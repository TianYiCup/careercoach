"""Scenario catalog service — PRD §7.3.

Public surface:
  * `ScenarioRecord` (data class shared with the seed module)
  * `ScenarioRepository` Protocol + `InMemoryScenarioRepository`
  * `ScenarioService` (list with filters)
  * `get_scenario_service()` + `get_scenario_repository()` factories

The session service (`app.services.sessions.scenario_seed`) reads the
same catalog directly for its sync `get_scenario_seed()` helper —
both surfaces see the same data without going through the async
repository protocol where it's not needed.
"""

from functools import lru_cache

from app.services.scenarios.repository import (
    InMemoryScenarioRepository,
    ScenarioRepository,
)
from app.services.scenarios.seed_data import ScenarioRecord
from app.services.scenarios.service import ScenarioService


@lru_cache(maxsize=1)
def get_scenario_repository() -> InMemoryScenarioRepository:
    """Singleton in-memory catalog. Swap to a DB-backed impl when the
    Postgres table lands; the protocol stays the same."""
    return InMemoryScenarioRepository()


@lru_cache(maxsize=1)
def get_scenario_service() -> ScenarioService:
    return ScenarioService(repository=get_scenario_repository())


__all__ = [
    "InMemoryScenarioRepository",
    "ScenarioRecord",
    "ScenarioRepository",
    "ScenarioService",
    "get_scenario_repository",
    "get_scenario_service",
]
