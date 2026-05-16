"""Review-mode persistence layer.

Two implementations: an in-memory store for unit tests + dev runs
without docker, and a SQLAlchemy-backed store for deployed envs.
The factory in `__init__.py` picks one via `settings.review_repo_backend`,
matching the `sessions_repo_backend` precedent.

Why this layer is separate from the route + agent layers
--------------------------------------------------------
A-9 lands the persistence shell *before* the reviewer LangGraph node
(A-10) and the route wiring (A-11). Splitting them keeps each PR
under the soft 800-LoC line and lets the agent + route work import
a stable repository surface instead of inventing it ad-hoc.

Read pattern
------------
The only read is "load one upload, ordered turns attached" — used by
`GET /v1/review/uploads/{id}` (A-11). `get` returns the full
`ReviewUploadRecord` with its `turns` tuple already populated; no
secondary query is required at the route layer.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime
from typing import Literal, Protocol, runtime_checkable

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.models.review import ReviewTurn, ReviewUpload

ReviewStatus = Literal["processing", "done", "failed"]
ReviewVerdict = Literal["win", "neutral", "lose"]
Speaker = Literal["user", "opponent"]


@dataclass(frozen=True)
class ReviewTurnRecord:
    """One analysed line in a review upload.

    `reason` and `better` are populated only for `lose` verdicts —
    PRD §3.3 US-C2 specifies "更佳话术" only on失分 turns. The route
    layer maps None → omitted-key in the JSON response.
    """

    turn_idx: int
    speaker: Speaker
    content: str
    verdict: ReviewVerdict
    reason: str | None = None
    better: str | None = None


@dataclass(frozen=True)
class ReviewUploadRecord:
    """Immutable snapshot of one review upload + its analysis result.

    `turns` is a tuple (not list) so callers cannot mutate the
    canonical state. `summary_*` fields are tuples for the same
    reason. While the upload is still `processing`, all of these
    are empty / None — `update_result` flips them in one call.
    """

    upload_id: str
    user_id: str
    text: str
    status: ReviewStatus
    turns: tuple[ReviewTurnRecord, ...]
    summary_score: float | None
    summary_top_failures: tuple[str, ...]
    summary_improvements: tuple[str, ...]
    created_at: datetime
    completed_at: datetime | None


@runtime_checkable
class ReviewRepository(Protocol):
    """Persistence seam — both InMemory and Postgres impls below."""

    async def create(self, record: ReviewUploadRecord) -> None: ...

    async def get(self, upload_id: str) -> ReviewUploadRecord | None: ...

    async def update_result(
        self,
        upload_id: str,
        *,
        turns: tuple[ReviewTurnRecord, ...],
        summary_score: float,
        summary_top_failures: tuple[str, ...],
        summary_improvements: tuple[str, ...],
        completed_at: datetime,
    ) -> None: ...

    async def mark_failed(
        self,
        upload_id: str,
        *,
        completed_at: datetime,
    ) -> None: ...


class InMemoryReviewRepository:
    """Dict-backed store. Single uvicorn worker is the only writer in v0,
    so no locks. Reuses the immutable `ReviewUploadRecord` directly —
    the dataclass-replace pattern prevents accidental mutation."""

    def __init__(self) -> None:
        self._store: dict[str, ReviewUploadRecord] = {}

    async def create(self, record: ReviewUploadRecord) -> None:
        self._store[record.upload_id] = record

    async def get(self, upload_id: str) -> ReviewUploadRecord | None:
        return self._store.get(upload_id)

    async def update_result(
        self,
        upload_id: str,
        *,
        turns: tuple[ReviewTurnRecord, ...],
        summary_score: float,
        summary_top_failures: tuple[str, ...],
        summary_improvements: tuple[str, ...],
        completed_at: datetime,
    ) -> None:
        existing = self._store.get(upload_id)
        if existing is None:
            return
        self._store[upload_id] = replace(
            existing,
            status="done",
            turns=turns,
            summary_score=summary_score,
            summary_top_failures=summary_top_failures,
            summary_improvements=summary_improvements,
            completed_at=completed_at,
        )

    async def mark_failed(
        self,
        upload_id: str,
        *,
        completed_at: datetime,
    ) -> None:
        existing = self._store.get(upload_id)
        if existing is None:
            return
        self._store[upload_id] = replace(
            existing,
            status="failed",
            completed_at=completed_at,
        )


class PostgresReviewRepository:
    """SQLAlchemy-backed implementation.

    `get` runs two queries (upload row + ordered turns) rather than a
    single JOIN — the row counts are small (≤ 50 turns) and the two
    statements keep the mappers below dead simple. `update_result`
    does a DELETE-then-bulk-INSERT against `review_turns` so re-runs
    of the reviewer chain are fully idempotent without per-row UPSERT
    bookkeeping.
    """

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def create(self, record: ReviewUploadRecord) -> None:
        async with self._session_factory() as session, session.begin():
            session.add(_upload_record_to_model(record))

    async def get(self, upload_id: str) -> ReviewUploadRecord | None:
        async with self._session_factory() as session:
            upload_stmt = select(ReviewUpload).where(ReviewUpload.upload_id == upload_id)
            upload_row = (await session.execute(upload_stmt)).scalar_one_or_none()
            if upload_row is None:
                return None
            turns_stmt = (
                select(ReviewTurn)
                .where(ReviewTurn.upload_id == upload_id)
                .order_by(ReviewTurn.turn_idx.asc())
            )
            turn_rows = (await session.execute(turns_stmt)).scalars().all()
        turns = tuple(_turn_model_to_record(t) for t in turn_rows)
        return _upload_model_to_record(upload_row, turns)

    async def update_result(
        self,
        upload_id: str,
        *,
        turns: tuple[ReviewTurnRecord, ...],
        summary_score: float,
        summary_top_failures: tuple[str, ...],
        summary_improvements: tuple[str, ...],
        completed_at: datetime,
    ) -> None:
        async with self._session_factory() as session, session.begin():
            upload = await session.get(ReviewUpload, upload_id)
            if upload is None:
                return
            upload.status = "done"
            upload.summary_score = summary_score
            upload.summary_top_failures = list(summary_top_failures)
            upload.summary_improvements = list(summary_improvements)
            upload.completed_at = completed_at
            await session.execute(delete(ReviewTurn).where(ReviewTurn.upload_id == upload_id))
            for turn in turns:
                session.add(_turn_record_to_model(upload_id, turn))

    async def mark_failed(
        self,
        upload_id: str,
        *,
        completed_at: datetime,
    ) -> None:
        async with self._session_factory() as session, session.begin():
            upload = await session.get(ReviewUpload, upload_id)
            if upload is None:
                return
            upload.status = "failed"
            upload.completed_at = completed_at


def _upload_record_to_model(record: ReviewUploadRecord) -> ReviewUpload:
    return ReviewUpload(
        upload_id=record.upload_id,
        user_id=record.user_id,
        text=record.text,
        status=record.status,
        summary_score=record.summary_score,
        summary_top_failures=list(record.summary_top_failures),
        summary_improvements=list(record.summary_improvements),
        created_at=record.created_at,
        completed_at=record.completed_at,
    )


def _upload_model_to_record(
    row: ReviewUpload,
    turns: tuple[ReviewTurnRecord, ...],
) -> ReviewUploadRecord:
    return ReviewUploadRecord(
        upload_id=row.upload_id,
        user_id=row.user_id,
        text=row.text,
        status=_coerce_status(row.status),
        turns=turns,
        summary_score=row.summary_score,
        summary_top_failures=tuple(row.summary_top_failures or ()),
        summary_improvements=tuple(row.summary_improvements or ()),
        created_at=row.created_at,
        completed_at=row.completed_at,
    )


def _turn_record_to_model(upload_id: str, record: ReviewTurnRecord) -> ReviewTurn:
    return ReviewTurn(
        upload_id=upload_id,
        turn_idx=record.turn_idx,
        speaker=record.speaker,
        content=record.content,
        verdict=record.verdict,
        reason=record.reason,
        better=record.better,
    )


def _turn_model_to_record(row: ReviewTurn) -> ReviewTurnRecord:
    return ReviewTurnRecord(
        turn_idx=row.turn_idx,
        speaker=_coerce_speaker(row.speaker),
        content=row.content,
        verdict=_coerce_verdict(row.verdict),
        reason=row.reason,
        better=row.better,
    )


# The DB column is plain String(16) so adding states never needs an
# ALTER TYPE migration. These narrowing helpers preserve the typed
# Literal at the boundary; an unexpected value (someone hand-edited
# the row) raises rather than silently corrupting downstream typing.
def _coerce_status(raw: str) -> ReviewStatus:
    if raw not in ("processing", "done", "failed"):
        raise ValueError(f"unknown review status: {raw!r}")
    return raw  # type: ignore[return-value]


def _coerce_verdict(raw: str) -> ReviewVerdict:
    if raw not in ("win", "neutral", "lose"):
        raise ValueError(f"unknown review verdict: {raw!r}")
    return raw  # type: ignore[return-value]


def _coerce_speaker(raw: str) -> Speaker:
    if raw not in ("user", "opponent"):
        raise ValueError(f"unknown review speaker: {raw!r}")
    return raw  # type: ignore[return-value]
