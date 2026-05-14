"""Scenario catalog data plane.

`InMemoryScenarioRepository` reads from the in-Python `SCENARIO_CATALOG`
constant. The protocol's the same shape a Postgres-backed repo will
expose, so the swap is local to this module when the DB lands.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from app.services.scenarios.seed_data import SCENARIO_CATALOG, ScenarioRecord


@runtime_checkable
class ScenarioRepository(Protocol):
    """Async lookups for the `/v1/scenarios` surface.

    `list_all` exists rather than `list(category, q)` because filtering
    is the service layer's job — keeping the protocol field-agnostic
    means a future SQL implementation can paginate without bending
    the filter contract.
    """

    async def list_all(self) -> list[ScenarioRecord]: ...

    async def get(self, scenario_id: str) -> ScenarioRecord | None: ...


class InMemoryScenarioRepository:
    """Reads straight off the in-Python catalog. Process-wide singleton
    via `get_scenario_repository`; mutating it isn't supported (the
    underlying tuple is immutable)."""

    def __init__(self, catalog: tuple[ScenarioRecord, ...] = SCENARIO_CATALOG) -> None:
        self._catalog = catalog
        self._by_id = {record.id: record for record in catalog}

    async def list_all(self) -> list[ScenarioRecord]:
        # Return a fresh list so callers can sort or slice without
        # leaking back into the singleton's view.
        return list(self._catalog)

    async def get(self, scenario_id: str) -> ScenarioRecord | None:
        return self._by_id.get(scenario_id)


__all__ = ["InMemoryScenarioRepository", "ScenarioRepository"]
