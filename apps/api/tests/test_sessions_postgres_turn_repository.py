"""Integration smoke for `PostgresTurnRepository`.

Skipped automatically when postgres isn't reachable. Each test seeds
its own session row (FK requirement) and cleans up after itself.

See `test_sessions_postgres_repository.py` for why this file builds
its own engine per test rather than reusing `app.db.engine`.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta

import pytest
import pytest_asyncio
from app.agents.state import TurnScore, Verdict
from app.config import get_settings
from app.models import Session as SessionRow
from app.models import Turn as TurnRow
from app.services.sessions.turn_repository import (
    CoachHintTrio,
    PostgresTurnRepository,
    TurnRecord,
)
from sqlalchemy import delete, text
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)


@pytest_asyncio.fixture
async def pg_factory() -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    engine = create_async_engine(
        get_settings().database_url,
        echo=False,
        pool_pre_ping=True,
        future=True,
    )
    try:
        async with engine.connect() as conn:
            await conn.execute(text("select 1"))
    except Exception as exc:
        await engine.dispose()
        pytest.skip(f"postgres unreachable at {get_settings().database_url}: {exc}")

    factory: async_sessionmaker[AsyncSession] = async_sessionmaker(
        bind=engine,
        expire_on_commit=False,
        autoflush=False,
        autocommit=False,
    )
    try:
        yield factory
    finally:
        await engine.dispose()


@pytest_asyncio.fixture
async def session_id(
    pg_factory: async_sessionmaker[AsyncSession],
) -> AsyncIterator[str]:
    """Seed a sessions row so turn FK inserts have a parent."""
    sid = f"ses_{uuid.uuid4().hex[:16]}"
    async with pg_factory() as session, session.begin():
        session.add(
            SessionRow(
                session_id=sid,
                user_id="u_pg_turn_test",
                mode="sandbox",
                scenario_id="sc_001",
                persona_id="p_hard",
                user_goal="保住周末",
                status="active",
                created_at=datetime.now(UTC),
            )
        )
    try:
        yield sid
    finally:
        async with pg_factory() as session, session.begin():
            # CASCADE on the FK cleans up turns automatically.
            await session.execute(delete(SessionRow).where(SessionRow.session_id == sid))


@pytest_asyncio.fixture
async def repo(
    pg_factory: async_sessionmaker[AsyncSession],
) -> AsyncIterator[PostgresTurnRepository]:
    yield PostgresTurnRepository(pg_factory)


def _record(
    *,
    session_id: str,
    turn_id: str | None = None,
    verdict: Verdict = Verdict.GUOLU,
    rating: int = 60,
    created_at: datetime | None = None,
) -> TurnRecord:
    return TurnRecord(
        turn_id=turn_id or f"t_{uuid.uuid4().hex[:8]}",
        session_id=session_id,
        user_content="周末有事",
        opponent_reply="项目谁顶？",
        coach_hint=CoachHintTrio(safe="稳住", aggressive="刚回去", humor="搞笑一下"),
        turn_score=TurnScore(verdict=verdict, rating=rating),
        created_at=created_at or datetime.now(UTC),
    )


@pytest.mark.integration
async def test_append_then_list_round_trips(repo: PostgresTurnRepository, session_id: str) -> None:
    record = _record(session_id=session_id)
    await repo.append(record)

    listed = await repo.list_for_session(session_id)
    assert len(listed) == 1
    loaded = listed[0]
    assert loaded.turn_id == record.turn_id
    assert loaded.user_content == "周末有事"
    assert loaded.coach_hint.safe == "稳住"
    assert loaded.coach_hint.humor == "搞笑一下"
    assert loaded.turn_score.verdict is Verdict.GUOLU
    assert loaded.turn_score.rating == 60


@pytest.mark.integration
async def test_list_returns_empty_for_unknown_session(
    repo: PostgresTurnRepository,
) -> None:
    assert await repo.list_for_session("ses_no_such_thing") == []


@pytest.mark.integration
async def test_list_orders_by_created_at_ascending(
    repo: PostgresTurnRepository, session_id: str
) -> None:
    """The aggregator + /turns history rebuild both assume insertion
    order matches created_at order — pin that explicitly here."""
    base = datetime.now(UTC)
    older = _record(
        session_id=session_id,
        turn_id="t_older001",
        created_at=base,
    )
    newer = _record(
        session_id=session_id,
        turn_id="t_newer001",
        created_at=base + timedelta(seconds=10),
    )

    # Insert in reverse-time order to make sure the query, not the
    # insertion order, drives the result.
    await repo.append(newer)
    await repo.append(older)

    listed = await repo.list_for_session(session_id)
    assert [t.turn_id for t in listed] == ["t_older001", "t_newer001"]


@pytest.mark.integration
async def test_each_verdict_round_trips_through_enum(
    repo: PostgresTurnRepository, session_id: str
) -> None:
    """Verdict is stored as plain text — make sure every literal in
    the enum survives the round trip."""
    for index, verdict in enumerate(Verdict):
        record = _record(
            session_id=session_id,
            turn_id=f"t_v_{index:02d}",
            verdict=verdict,
            rating=50 + index,
            created_at=datetime.now(UTC) + timedelta(seconds=index),
        )
        await repo.append(record)

    listed = await repo.list_for_session(session_id)
    assert {t.turn_score.verdict for t in listed} == set(Verdict)


@pytest.mark.integration
async def test_cascade_delete_on_session_clears_turns(
    pg_factory: async_sessionmaker[AsyncSession],
    session_id: str,
) -> None:
    """Sessions table FK has ON DELETE CASCADE — wiping a session must
    take its turns with it. This is the audit story for moderation
    events being separate (they don't cascade)."""
    repo = PostgresTurnRepository(pg_factory)
    record = _record(session_id=session_id)
    await repo.append(record)

    async with pg_factory() as session, session.begin():
        await session.execute(delete(SessionRow).where(SessionRow.session_id == session_id))

    async with pg_factory() as session, session.begin():
        result = await session.execute(delete(TurnRow).where(TurnRow.turn_id == record.turn_id))
        # nothing left to delete after the cascade
        assert result.rowcount == 0
