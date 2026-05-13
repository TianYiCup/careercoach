"""Per-scenario seed data — opening line + display titles.

Used by the session service while `/v1/scenarios` is still a 501 stub
and `/v1/sessions/{id}/turns` doesn't talk to an LLM yet. Sprint-2 swaps
this for a database-backed `ScenarioRepository`.

The IDs mirror `apps/web/src/mocks/handlers/api.ts` so frontend mock and
backend real wiring tell the same story across the seam.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ScenarioSeed:
    """Minimum data the session service needs at create + end time."""

    scenario_title: str
    persona_title: str
    opening_line: str


# Sprint-1 catalog. Sprint-2 PR replaces with a DB read; the dict keeps
# the same shape (id → ScenarioSeed) so the swap is local to this file.
SCENARIO_SEEDS: dict[str, ScenarioSeed] = {
    "sc_001": ScenarioSeed(
        scenario_title="周末加班谈判",
        persona_title="强硬型 HR",
        opening_line="小林啊，这个周末项目得加个班，应该没问题吧？",
    ),
    "sc_002": ScenarioSeed(
        scenario_title="实习转正薪资谈判",
        persona_title="老 HR",
        opening_line="坐吧，转正的事情我们聊聊。你的期望薪资是多少？",
    ),
    "sc_003": ScenarioSeed(
        scenario_title="室友深夜打游戏",
        persona_title="同寝室友",
        opening_line="嘿，再来一把？这把一定赢！",
    ),
}


_FALLBACK = ScenarioSeed(
    scenario_title="自由练习",
    persona_title="陌生对手",
    opening_line="我们来聊聊吧。",
)


def get_scenario_seed(scenario_id: str) -> ScenarioSeed:
    """Return the seed for `scenario_id`, or a generic fallback.

    Unknown scenarios don't fail the session create — sprint-1 still
    wants to demo "any string in works" while the catalog is sparse.
    """
    return SCENARIO_SEEDS.get(scenario_id, _FALLBACK)
