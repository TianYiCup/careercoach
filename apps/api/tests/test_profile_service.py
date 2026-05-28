"""Tests for the L5 ProfileService + InMemory repository."""

from __future__ import annotations

from datetime import UTC, datetime

from app.services.profile import InMemoryProfileRepository, ProfileService
from app.services.scenarios.character_vector import CharacterVector

NOW = datetime(2026, 5, 28, 12, 0, tzinfo=UTC)

HR = CharacterVector(
    aggression=60,
    empathy=30,
    control=75,
    honesty=50,
    stability=80,
    power_gap=70,
)


def _service() -> ProfileService:
    return ProfileService(repo=InMemoryProfileRepository())


async def test_record_accumulates_count_and_effect_tallies() -> None:
    svc = _service()
    await svc.record_safe(user_id="u1", strategy="placate", effect="poor", now=NOW)
    await svc.record_safe(user_id="u1", strategy="placate", effect="good", now=NOW)
    await svc.record_safe(user_id="u1", strategy="placate", effect="poor", now=NOW)

    stats = await svc.get_stats("u1")
    assert len(stats) == 1
    row = stats[0]
    assert row.count == 3
    assert row.poor == 2
    assert row.good == 1
    assert row.win_rate == 1 / 3


async def test_stats_sorted_by_count_desc() -> None:
    svc = _service()
    await svc.record_safe(user_id="u1", strategy="direct", effect="good", now=NOW)
    for _ in range(3):
        await svc.record_safe(user_id="u1", strategy="placate", effect="poor", now=NOW)

    stats = await svc.get_stats("u1")
    assert [s.strategy for s in stats] == ["placate", "direct"]


async def test_get_stats_empty_for_new_user() -> None:
    svc = _service()
    assert await svc.get_stats("nobody") == []


async def test_summary_reports_total_and_overrelied_crutch() -> None:
    svc = _service()
    # placate used 4x, mostly poor → the crutch.
    for effect in ("poor", "poor", "poor", "good"):
        await svc.record_safe(user_id="u1", strategy="placate", effect=effect, now=NOW)
    await svc.record_safe(user_id="u1", strategy="direct", effect="good", now=NOW)

    summary = await svc.get_summary("u1")
    assert summary.total_observations == 5
    assert summary.overrelied_strategy == "placate"


async def test_overrelied_requires_poor_majority() -> None:
    """A heavily-used but *working* strategy is not a crutch."""
    svc = _service()
    for _ in range(5):
        await svc.record_safe(user_id="u1", strategy="direct", effect="good", now=NOW)

    summary = await svc.get_summary("u1")
    assert summary.overrelied_strategy is None


async def test_overrelied_requires_min_count() -> None:
    """Two poor plays isn't enough signal to call it a crutch."""
    svc = _service()
    await svc.record_safe(user_id="u1", strategy="placate", effect="poor", now=NOW)
    await svc.record_safe(user_id="u1", strategy="placate", effect="poor", now=NOW)

    summary = await svc.get_summary("u1")
    assert summary.overrelied_strategy is None


async def test_adapt_vector_softens_for_new_user() -> None:
    svc = _service()
    adapted = await svc.adapt_vector(user_id="newbie", base=HR)

    # No observations → softened toward neutral.
    assert adapted.stability < HR.stability
    assert adapted.control < HR.control


async def test_adapt_vector_hardens_against_crutch_for_veteran() -> None:
    svc = _service()
    # 20 observations all on placate-poor → veteran + clear crutch.
    for _ in range(20):
        await svc.record_safe(user_id="u1", strategy="placate", effect="poor", now=NOW)

    adapted = await svc.adapt_vector(user_id="u1", base=HR)
    # Veteran → full intensity; placate crutch → hardened power_gap/control.
    assert adapted.power_gap >= HR.power_gap
    assert adapted.control >= HR.control
    assert adapted.empathy <= HR.empathy


async def test_unknown_effect_is_dropped() -> None:
    """A bad effect key (defensive) records nothing rather than crashing."""
    svc = _service()
    await svc.record_safe(user_id="u1", strategy="placate", effect="devastating", now=NOW)

    assert await svc.get_stats("u1") == []
