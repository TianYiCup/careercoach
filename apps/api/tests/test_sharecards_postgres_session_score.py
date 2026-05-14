"""Integration smoke for `PostgresSessionScoreRepository`.

Skipped automatically when postgres isn't reachable. The session
score table FK references the sessions table, so each test seeds a
parent session row before inserting scores. CASCADE on delete keeps
cleanup honest — wiping the sessions row drops its score with it.

Engine-per-test fixture mirrors PR #46's pattern.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta

import pytest
import pytest_asyncio
from app.config import get_settings
from app.models import Session as SessionRow
from app.services.sharecards.session_score import PostgresSessionScoreRepository
from app.services.sharecards.types import SessionCardData
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
    """Seed a parent sessions row so score INSERT can satisfy the FK."""
    sid = f"ses_{uuid.uuid4().hex[:16]}"
    async with pg_factory() as session, session.begin():
        session.add(
            SessionRow(
                session_id=sid,
                user_id="u_pg_score_test",
                mode="sandbox",
                scenario_id="sc_001",
                persona_id="p_hard",
                user_goal="保住周末",
                status="ended",
                created_at=datetime.now(UTC),
            )
        )
    try:
        yield sid
    finally:
        async with pg_factory() as session, session.begin():
            # CASCADE drops the matching score row automatically.
            await session.execute(delete(SessionRow).where(SessionRow.session_id == sid))


@pytest_asyncio.fixture
async def repo(
    pg_factory: async_sessionmaker[AsyncSession],
) -> AsyncIterator[PostgresSessionScoreRepository]:
    yield PostgresSessionScoreRepository(pg_factory)


def _data(*, result: str = "shenfeng") -> SessionCardData:
    return SessionCardData(
        scenario_title="周末加班谈判",
        persona_title="强硬型 HR",
        aura=9,
        logic=8,
        emotion=8,
        professionalism=9,
        goal_achieve=8,
        result=result,  # type: ignore[arg-type]
        highlights="顶住了压力还反将了一军",
    )


@pytest.mark.integration
async def test_add_then_get_round_trips(
    repo: PostgresSessionScoreRepository,
    session_id: str,
) -> None:
    await repo.add(session_id, _data())

    loaded = await repo.get(session_id, user_id="u_pg_score_test")
    assert loaded is not None
    assert loaded.scenario_title == "周末加班谈判"
    assert loaded.persona_title == "强硬型 HR"
    assert loaded.aura == 9
    assert loaded.result == "shenfeng"
    assert loaded.highlights == "顶住了压力还反将了一军"


@pytest.mark.integration
async def test_get_unknown_session_returns_none(
    repo: PostgresSessionScoreRepository,
) -> None:
    assert await repo.get("ses_no_such_score", user_id="u_x") is None


@pytest.mark.integration
async def test_add_is_idempotent_for_same_session(
    repo: PostgresSessionScoreRepository,
    session_id: str,
) -> None:
    """A retry of `/end` on the same session must update the cached
    score rather than 500 on a PK conflict."""
    await repo.add(session_id, _data(result="guolu"))
    await repo.add(session_id, _data(result="shenfeng"))

    loaded = await repo.get(session_id, user_id="u_pg_score_test")
    assert loaded is not None
    assert loaded.result == "shenfeng"


@pytest.mark.integration
async def test_each_verdict_round_trips(
    repo: PostgresSessionScoreRepository,
    pg_factory: async_sessionmaker[AsyncSession],
) -> None:
    """`result` is plain text; make sure every literal the schema
    accepts survives the round trip."""
    session_ids: list[str] = []
    try:
        for verdict in ("shenfeng", "guolu", "fanche"):
            sid = f"ses_{uuid.uuid4().hex[:16]}"
            session_ids.append(sid)
            async with pg_factory() as session, session.begin():
                session.add(
                    SessionRow(
                        session_id=sid,
                        user_id="u_verdict_test",
                        mode="sandbox",
                        scenario_id="sc_001",
                        persona_id="p_hard",
                        user_goal="保住周末",
                        status="ended",
                        created_at=datetime.now(UTC),
                    )
                )
            await repo.add(sid, _data(result=verdict))

            loaded = await repo.get(sid, user_id="u_verdict_test")
            assert loaded is not None
            assert loaded.result == verdict
    finally:
        async with pg_factory() as session, session.begin():
            await session.execute(delete(SessionRow).where(SessionRow.session_id.in_(session_ids)))


@pytest.mark.integration
async def test_list_for_user_filters_by_window(
    repo: PostgresSessionScoreRepository,
    pg_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Weekly + wrapped both call `list_for_user(since, until)`; pin
    that the time bucketing actually works."""
    session_ids: list[str] = []
    try:
        # Three sessions; we'll filter the window to capture only the middle one.
        for _ in range(3):
            sid = f"ses_{uuid.uuid4().hex[:16]}"
            session_ids.append(sid)
            async with pg_factory() as session, session.begin():
                session.add(
                    SessionRow(
                        session_id=sid,
                        user_id="u_window_test",
                        mode="sandbox",
                        scenario_id="sc_001",
                        persona_id="p_hard",
                        user_goal="保住周末",
                        status="ended",
                        created_at=datetime.now(UTC),
                    )
                )
            await repo.add(sid, _data())

        # All three created within the last few seconds; an empty
        # window in the past should return zero rows.
        far_past_until = datetime.now(UTC) - timedelta(days=1)
        empty = await repo.list_for_user("u_window_test", until=far_past_until)
        assert empty == []

        # A window from 1 minute ago through 1 minute in the future
        # should capture all three.
        recent = await repo.list_for_user(
            "u_window_test",
            since=datetime.now(UTC) - timedelta(minutes=1),
            until=datetime.now(UTC) + timedelta(minutes=1),
        )
        assert len(recent) == 3
    finally:
        async with pg_factory() as session, session.begin():
            await session.execute(delete(SessionRow).where(SessionRow.session_id.in_(session_ids)))


@pytest.mark.integration
async def test_cascade_delete_on_session_clears_score(
    pg_factory: async_sessionmaker[AsyncSession],
    session_id: str,
) -> None:
    """Wiping a session row must take its cached score with it. Same
    cascade story as the turns table — moderation events are the
    standalone audit trail that doesn't cascade."""
    repo = PostgresSessionScoreRepository(pg_factory)
    await repo.add(session_id, _data())
    # Confirm it landed.
    loaded = await repo.get(session_id, user_id="u_pg_score_test")
    assert loaded is not None

    async with pg_factory() as session, session.begin():
        await session.execute(delete(SessionRow).where(SessionRow.session_id == session_id))

    assert await repo.get(session_id, user_id="u_pg_score_test") is None
