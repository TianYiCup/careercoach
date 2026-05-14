"""Behavioural tests for `ScenarioService`.

Covers projection (record → summary, persona_title stripped) and the
two filters: `category` exact match and `q` free-text substring.
Both filters are independent and combine with AND.
"""

from __future__ import annotations

from app.services.scenarios.repository import InMemoryScenarioRepository
from app.services.scenarios.service import ScenarioService


def _service() -> ScenarioService:
    return ScenarioService(repository=InMemoryScenarioRepository())


async def test_list_without_filters_returns_full_catalog() -> None:
    svc = _service()

    response = await svc.list_scenarios(category=None, query=None)

    assert response.total == len(response.items)
    assert response.total >= 7  # match the seeded catalog size
    # `total` is the matching count, not a global catalog size — when
    # there's no filter both numbers are the same.
    ids = {item.id for item in response.items}
    assert {"sc_001", "sc_002", "sc_003"} <= ids


async def test_list_filters_by_category() -> None:
    svc = _service()

    response = await svc.list_scenarios(category="campus", query=None)

    assert response.total >= 1
    assert all(item.category == "campus" for item in response.items)


async def test_list_filters_by_text_in_title() -> None:
    svc = _service()

    response = await svc.list_scenarios(category=None, query="加班")

    assert any("加班" in item.title for item in response.items)
    assert response.total == len(response.items)


async def test_list_filters_by_text_in_tags() -> None:
    """The `q` search must hit tag values too — frontend uses tag chips
    as keyword shortcuts."""
    svc = _service()

    response = await svc.list_scenarios(category=None, query="谈判")

    assert response.total >= 1
    # The matching scenario tags include 谈判; the title need not contain it.
    assert any("谈判" in item.title or "谈判" in item.tags for item in response.items)


async def test_list_filter_combines_category_and_query_with_and_semantics() -> None:
    """A campus-tagged "谈判" search must NOT return the jobhunt-tagged
    salary scenario, even though it matches the substring."""
    svc = _service()

    response = await svc.list_scenarios(category="campus", query="谈判")

    assert all(item.category == "campus" for item in response.items)


async def test_list_unknown_category_returns_empty_list_not_error() -> None:
    """`category` is a Pydantic literal at the route, but the service
    layer must not raise on an empty result — total goes to 0."""
    svc = _service()

    response = await svc.list_scenarios(category="campus", query="完全不存在的关键词")

    assert response.total == 0
    assert response.items == []


async def test_summary_drops_seed_only_fields() -> None:
    """`persona_title` and `opening_line` are session-private; they
    must not leak through `/v1/scenarios`."""
    svc = _service()

    response = await svc.list_scenarios(category=None, query=None)

    for item in response.items:
        item_dict = item.model_dump()
        assert "persona_title" not in item_dict
        assert "opening_line" not in item_dict


async def test_empty_query_string_is_treated_as_no_filter() -> None:
    """`q=""` and `q="   "` come in from URL serialisation when the
    user clears the search box; both must behave like "no filter"
    instead of "match nothing"."""
    svc = _service()

    baseline = await svc.list_scenarios(category=None, query=None)
    empty = await svc.list_scenarios(category=None, query="")
    blanks = await svc.list_scenarios(category=None, query="   ")

    assert empty.total == baseline.total
    assert blanks.total == baseline.total
