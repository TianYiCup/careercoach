"""Per-scenario seed data — opening line + display titles.

Thin adapter on top of `app.services.scenarios.seed_data` so the
session create / end flow and the `/v1/scenarios` route share one
catalog. The split (sync getter here, async repository there) exists
because the session pipeline runs synchronously inside the request
handler before any async boundary; making this awaitable would push
that change through `SessionService.create_session`,
`SessionService.end_session`, and `TurnService._build_history`
without buying us anything.

When the catalog moves to Postgres, this module gains an async
loader that warms a process-local cache from the DB on startup;
callers keep the sync signature.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.services.scenarios.seed_data import get_record_by_id


@dataclass(frozen=True)
class ScenarioSeed:
    """The subset of fields the session service needs at create + end."""

    scenario_title: str
    persona_title: str
    opening_line: str


def get_scenario_seed(scenario_id: str) -> ScenarioSeed:
    """Return the seed for `scenario_id`, or a generic fallback.

    Unknown ids fall back rather than 404 so demo flows ("any string in
    works") still produce a session while the catalog is sparse.
    """
    record = get_record_by_id(scenario_id)
    return ScenarioSeed(
        scenario_title=record.scenario_title,
        persona_title=record.persona_title,
        opening_line=record.opening_line,
    )


__all__ = ["ScenarioSeed", "get_scenario_seed"]
