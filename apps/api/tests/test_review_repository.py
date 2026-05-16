"""Unit tests for `InMemoryReviewRepository`.

A-9 ships in-memory only. These tests double as the conformance
contract a SQL-backed implementation must also pass — the matching
Postgres test file lands when the docker stack is wired up in CI.
"""

from __future__ import annotations

from datetime import UTC, datetime

from app.services.review import (
    InMemoryReviewRepository,
    ReviewTurnRecord,
    ReviewUploadRecord,
)

_CREATED_AT = datetime(2026, 5, 16, 9, 0, tzinfo=UTC)
_COMPLETED_AT = datetime(2026, 5, 16, 9, 0, 30, tzinfo=UTC)


def _processing_record(upload_id: str = "up_aaaa1111") -> ReviewUploadRecord:
    """Mint a fresh upload row in the `processing` state, mimicking
    what `POST /v1/review/uploads` will write before A-10 runs."""
    return ReviewUploadRecord(
        upload_id=upload_id,
        user_id="u_demo",
        text="opponent: free this weekend?\nme: busy, raincheck.",
        status="processing",
        turns=(),
        summary_score=None,
        summary_top_failures=(),
        summary_improvements=(),
        created_at=_CREATED_AT,
        completed_at=None,
    )


async def test_create_then_get_round_trips_record() -> None:
    repo = InMemoryReviewRepository()
    record = _processing_record()
    await repo.create(record)

    got = await repo.get("up_aaaa1111")
    assert got == record


async def test_get_unknown_upload_returns_none() -> None:
    repo = InMemoryReviewRepository()
    assert await repo.get("up_does_not_exist") is None


async def test_update_result_populates_turns_and_summary() -> None:
    repo = InMemoryReviewRepository()
    await repo.create(_processing_record())

    turns = (
        ReviewTurnRecord(turn_idx=0, speaker="opponent", content="free?", verdict="neutral"),
        ReviewTurnRecord(
            turn_idx=1,
            speaker="user",
            content="busy.",
            verdict="lose",
            reason="too curt, no warmth",
            better="have plans, can we Tuesday instead?",
        ),
    )
    await repo.update_result(
        "up_aaaa1111",
        turns=turns,
        summary_score=6.4,
        summary_top_failures=("too curt",),
        summary_improvements=("offer alt slot",),
        completed_at=_COMPLETED_AT,
    )

    got = await repo.get("up_aaaa1111")
    assert got is not None
    assert got.status == "done"
    assert got.turns == turns
    assert got.summary_score == 6.4
    assert got.summary_top_failures == ("too curt",)
    assert got.summary_improvements == ("offer alt slot",)
    assert got.completed_at == _COMPLETED_AT


async def test_update_result_replaces_existing_turns_idempotent() -> None:
    """The reviewer chain (A-10) is allowed to retry — a second
    update_result call must fully replace the first one's turns,
    not append to them. Mirrors the DELETE+INSERT semantics the
    Postgres impl uses."""
    repo = InMemoryReviewRepository()
    await repo.create(_processing_record())

    first_turns = (
        ReviewTurnRecord(turn_idx=0, speaker="user", content="v1 line", verdict="neutral"),
    )
    await repo.update_result(
        "up_aaaa1111",
        turns=first_turns,
        summary_score=5.0,
        summary_top_failures=("v1",),
        summary_improvements=("v1 fix",),
        completed_at=_COMPLETED_AT,
    )

    second_turns = (
        ReviewTurnRecord(turn_idx=0, speaker="opponent", content="v2 a", verdict="win"),
        ReviewTurnRecord(turn_idx=1, speaker="user", content="v2 b", verdict="neutral"),
    )
    await repo.update_result(
        "up_aaaa1111",
        turns=second_turns,
        summary_score=7.7,
        summary_top_failures=("v2",),
        summary_improvements=("v2 fix",),
        completed_at=_COMPLETED_AT,
    )

    got = await repo.get("up_aaaa1111")
    assert got is not None
    assert got.turns == second_turns
    assert got.summary_score == 7.7


async def test_mark_failed_sets_status_and_completed_at() -> None:
    repo = InMemoryReviewRepository()
    await repo.create(_processing_record())

    await repo.mark_failed("up_aaaa1111", completed_at=_COMPLETED_AT)

    got = await repo.get("up_aaaa1111")
    assert got is not None
    assert got.status == "failed"
    assert got.completed_at == _COMPLETED_AT
    assert got.turns == ()
    assert got.summary_score is None


async def test_update_result_on_unknown_upload_is_silent_noop() -> None:
    """Repos are dumb stores — they do not raise on missing rows.
    Caller responsibility (the reviewer chain) is to fetch first
    and decide whether the upload is still valid to update."""
    repo = InMemoryReviewRepository()

    await repo.update_result(
        "up_unknown",
        turns=(),
        summary_score=0.0,
        summary_top_failures=(),
        summary_improvements=(),
        completed_at=_COMPLETED_AT,
    )

    assert await repo.get("up_unknown") is None
