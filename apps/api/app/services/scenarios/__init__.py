"""Scenario catalog service — PRD §7.3.

Public surface:
  * `ScenarioRecord` (data class shared with the seed module)
  * `ScenarioRepository` Protocol + `InMemoryScenarioRepository`
  * `ScenarioService` (list with filters)
  * `CustomScenarioService` (POST /v1/scenarios/custom) + its errors
  * `get_scenario_service()` / `get_scenario_repository()` /
    `get_custom_scenario_service()` factories

The session service (`app.services.sessions.scenario_seed`) reads the
same catalog directly for its sync `get_scenario_seed()` helper —
both surfaces see the same data without going through the async
repository protocol where it's not needed.
"""

from functools import lru_cache

from app.services.moderation import get_moderation_service
from app.services.scenarios.custom import (
    CustomScenarioBlockedError,
    CustomScenarioRateLimitedError,
    CustomScenarioService,
)
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


@lru_cache(maxsize=1)
def get_custom_scenario_service() -> CustomScenarioService:
    """Default wiring for `POST /v1/scenarios/custom`. Singleton so the
    per-user daily quota counter is shared across requests."""
    return CustomScenarioService(moderation=get_moderation_service())


__all__ = [
    "CustomScenarioBlockedError",
    "CustomScenarioRateLimitedError",
    "CustomScenarioService",
    "InMemoryScenarioRepository",
    "ScenarioRecord",
    "ScenarioRepository",
    "ScenarioService",
    "get_custom_scenario_service",
    "get_scenario_repository",
    "get_scenario_service",
]
