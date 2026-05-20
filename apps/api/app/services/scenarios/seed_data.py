"""Canonical in-Python scenario catalog.

Single source of truth for both surfaces:
  * `GET /v1/scenarios` — listed and filtered by `ScenarioService`.
  * Session create / end — `scenario_seed.get_scenario_seed()` reads
    the same records to look up persona_title + opening_line.

When a DB-backed implementation lands, this module shrinks to the
seed-loader for the migration; the repository protocol stays the
same so callers don't change.

Covers all four `ScenarioCategory` literals so /v1/scenarios filter
tests have at least one match per band.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class ScenarioRecord:
    """All fields a `/v1/scenarios` row carries, plus the seed bits
    the session service needs at create + end time.

    Splitting `title` (used by both summary and seed) means the
    persona_title + opening_line live with the same identifier so a
    DB rename of the title only happens once.
    """

    id: str
    title: str
    category: str  # campus | jobhunt | intern | life — checked via Pydantic literal
    difficulty: int  # 1-5
    tags: tuple[str, ...]
    background: str
    real_user_certified: bool
    persona_title: str
    opening_line: str

    # Convenience so callers can do `record.scenario_title` to match the
    # PR-4a-vintage seed shape without renaming the canonical field.
    @property
    def scenario_title(self) -> str:
        return self.title


@dataclass(frozen=True)
class _FallbackRecord:
    """Used when an unknown `scenario_id` comes in — sprint-1 still
    wants the session create to succeed so the demo flow doesn't 404
    on a typo. Carries only the seed-side fields; never surfaced via
    `/v1/scenarios` listings.
    """

    title: str = "自由练习"
    persona_title: str = "陌生对手"
    opening_line: str = "我们来聊聊吧。"
    # Mirrored fields so `_FallbackRecord` can stand in for
    # `ScenarioRecord` at the seed lookup site.
    id: str = "sc_unknown"
    category: str = "life"
    difficulty: int = 2
    tags: tuple[str, ...] = field(default_factory=tuple)
    background: str = "未匹配到场景库中的条目，进入自由练习。"
    real_user_certified: bool = False

    @property
    def scenario_title(self) -> str:
        return self.title


FALLBACK_RECORD = _FallbackRecord()


# Seven scenarios spanning all four ScenarioCategory literals. The
# first three keep their PR 4a ids and copy so the MSW handlers
# (`apps/web/src/mocks/handlers/api.ts`) and any cached frontend
# fixtures continue to line up.
SCENARIO_CATALOG: tuple[ScenarioRecord, ...] = (
    ScenarioRecord(
        id="sc_001",
        title="周末加班谈判",
        category="intern",
        difficulty=3,
        tags=("拒绝", "上下级"),
        background="你刚结束周五的项目，老板在群里@你让周末加班赶进度。",
        real_user_certified=True,
        persona_title="强硬型 HR",
        opening_line="小林啊，这个周末项目得加个班，应该没问题吧？",
    ),
    ScenarioRecord(
        id="sc_002",
        title="实习转正薪资谈判",
        category="jobhunt",
        difficulty=4,
        tags=("薪资", "谈判"),
        background="实习期结束，HR 约你聊转正，薪资比你预期低 30%。",
        real_user_certified=True,
        persona_title="老 HR",
        opening_line="坐吧，转正的事情我们聊聊。你的期望薪资是多少？",
    ),
    ScenarioRecord(
        id="sc_003",
        title="室友深夜打游戏",
        category="campus",
        difficulty=2,
        tags=("室友", "沟通"),
        background="室友每天打游戏到凌晨 2 点，你明天有早八。",
        real_user_certified=False,
        persona_title="同寝室友",
        opening_line="嘿，再来一把？这把一定赢！",
    ),
    ScenarioRecord(
        id="sc_004",
        title="导师让无偿干私活",
        category="campus",
        difficulty=4,
        tags=("导师", "边界"),
        background="导师私下让你帮他做横向项目，没有任何报酬。",
        real_user_certified=False,
        persona_title="强势导师",
        opening_line="这个项目你来负责一下吧，对你以后申博也有帮助。",
    ),
    ScenarioRecord(
        id="sc_005",
        title="面试自我介绍",
        category="jobhunt",
        difficulty=1,
        tags=("面试", "自我介绍"),
        background="第一次校招面试，面试官让你做 2 分钟自我介绍。",
        real_user_certified=False,
        persona_title="资深面试官",
        opening_line="你好，先做一个 2 分钟的自我介绍吧。",
    ),
    ScenarioRecord(
        id="sc_006",
        title="父母催考公务员",
        category="life",
        difficulty=3,
        tags=("家人", "职业选择"),
        background="父母觉得稳定最重要，要你毕业就考公，你想做产品。",
        real_user_certified=False,
        persona_title="操心的母亲",
        opening_line="你看小明都考上了，你怎么还不抓紧准备？",
    ),
    ScenarioRecord(
        id="sc_007",
        title="房东恶意涨房租",
        category="life",
        difficulty=4,
        tags=("租房", "议价"),
        background="租约到期前一周，房东突然要涨 30% 房租。",
        real_user_certified=False,
        persona_title="精打细算的房东",
        opening_line="今年市场都涨了，明年房租按 1500 收吧。",
    ),
)


_BY_ID: dict[str, ScenarioRecord] = {record.id: record for record in SCENARIO_CATALOG}

# In-process registry for user-created scenarios (POST /v1/scenarios/
# custom). v1 keeps these in memory — single uvicorn worker, and the
# `*_repo_backend` knobs all default to `memory`. Durable storage is a
# follow-up (see the `app.services.sessions.scenario_seed` docstring).
_CUSTOM: dict[str, ScenarioRecord] = {}


def register_custom_scenario(record: ScenarioRecord) -> None:
    """Make a user-created scenario resolvable by `get_record_by_id`, so
    `POST /v1/sessions` can immediately practise against its id."""
    _CUSTOM[record.id] = record


def get_record_by_id(scenario_id: str) -> ScenarioRecord | _FallbackRecord:
    """Return the row for `scenario_id` — a registered custom scenario,
    a static catalog entry, or the fallback (so unknown ids don't break
    session create; sprint-1 wants "any string in works" for demos)."""
    custom = _CUSTOM.get(scenario_id)
    if custom is not None:
        return custom
    return _BY_ID.get(scenario_id, FALLBACK_RECORD)


__all__ = [
    "FALLBACK_RECORD",
    "SCENARIO_CATALOG",
    "ScenarioRecord",
    "get_record_by_id",
    "register_custom_scenario",
]
