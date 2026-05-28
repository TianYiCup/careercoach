"""Tests for the L6 MemoryService + episode recall note."""

from __future__ import annotations

from datetime import UTC, datetime

from app.services.memory import (
    EpisodeRecord,
    InMemoryEpisodeRepository,
    MemoryService,
    build_memory_note,
)

NOW = datetime(2026, 5, 29, 9, 0, tzinfo=UTC)


def _service() -> MemoryService:
    return MemoryService(repo=InMemoryEpisodeRepository())


async def test_recall_none_for_first_visit() -> None:
    svc = _service()
    assert await svc.recall(user_id="u1", scenario_id="sc_001") is None


async def test_record_then_recall() -> None:
    svc = _service()
    await svc.record_safe(
        user_id="u1", scenario_id="sc_001", result="fanche", takeaway="过早让步", now=NOW
    )

    episode = await svc.recall(user_id="u1", scenario_id="sc_001")
    assert episode is not None
    assert episode.visit_count == 1
    assert episode.last_result == "fanche"
    assert episode.last_takeaway == "过早让步"


async def test_visit_count_accumulates_and_latest_overwrites() -> None:
    svc = _service()
    await svc.record_safe(
        user_id="u1", scenario_id="sc_001", result="fanche", takeaway="过早让步", now=NOW
    )
    await svc.record_safe(
        user_id="u1", scenario_id="sc_001", result="shenfeng", takeaway="这次稳住了", now=NOW
    )

    episode = await svc.recall(user_id="u1", scenario_id="sc_001")
    assert episode is not None
    assert episode.visit_count == 2
    # Latest outcome overwrites.
    assert episode.last_result == "shenfeng"
    assert episode.last_takeaway == "这次稳住了"


async def test_recall_is_scoped_per_scenario() -> None:
    svc = _service()
    await svc.record_safe(
        user_id="u1", scenario_id="sc_001", result="fanche", takeaway="x", now=NOW
    )

    assert await svc.recall(user_id="u1", scenario_id="sc_002") is None


async def test_recall_is_scoped_per_user() -> None:
    svc = _service()
    await svc.record_safe(
        user_id="u1", scenario_id="sc_001", result="fanche", takeaway="x", now=NOW
    )

    assert await svc.recall(user_id="u2", scenario_id="sc_001") is None


async def test_takeaway_truncated_to_column_limit() -> None:
    svc = _service()
    await svc.record_safe(
        user_id="u1", scenario_id="sc_001", result="guolu", takeaway="字" * 500, now=NOW
    )

    episode = await svc.recall(user_id="u1", scenario_id="sc_001")
    assert episode is not None
    assert len(episode.last_takeaway) == 300


# --- build_memory_note --------------------------------------------------------


def test_note_empty_for_no_episode() -> None:
    assert build_memory_note(None) == ""


def test_note_carries_visit_count_outcome_and_takeaway() -> None:
    episode = EpisodeRecord(
        id="ep_1",
        user_id="u1",
        scenario_id="sc_001",
        visit_count=3,
        last_result="fanche",
        last_takeaway="过早让步",
        last_seen=NOW,
    )

    note = build_memory_note(episode)

    assert "3 次" in note
    assert "翻了车" in note  # fanche gloss
    assert "过早让步" in note


def test_note_handles_unknown_result_gloss() -> None:
    """An unexpected verdict still produces a usable note (just no
    outcome clause), never a crash."""
    episode = EpisodeRecord(
        id="ep_1",
        user_id="u1",
        scenario_id="sc_001",
        visit_count=1,
        last_result="unknown_verdict",
        last_takeaway="",
        last_seen=NOW,
    )

    note = build_memory_note(episode)
    assert "1 次" in note
