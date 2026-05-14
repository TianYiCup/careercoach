"""Integration smoke for `PostgresSessionRepository`.

Skipped automatically when the configured postgres isn't reachable, so
the same file is safe locally without docker and tight in CI when
postgres is booted. Mirrors the pattern in `test_db_smoke.py`.

Engine-per-test note
--------------------
We create a fresh `AsyncEngine` inside the test fixture rather than
reusing `app.db.engine`. pytest-asyncio gives each test its own event
loop by default, and the module-level engine's connection pool — born
inside the FIRST test's loop — becomes unreachable from the SECOND
test's loop ("NoneType has no attribute 'send'" inside asyncpg). The
per-test engine pays a connection setup cost but keeps the tests
event-loop-coherent.

Every test cleans up after itself so re-running on a real DB doesn't
accumulate row debt.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta

import pytest
import pytest_asyncio
from app.config import get_settings
from app.models import Session as SessionRow
from app.services.sessions.repository import (
    PostgresSessionRepository,
    SessionRecord,
)
from sqlalchemy import delete, text
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)


@pytest_asyncio.fixture
async def pg_factory() -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    """Per-test engine + sessionmaker. Skips the whole test when PG is
    unreachable so the file degrades gracefully without docker."""
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
async def repo(
    pg_factory: async_sessionmaker[AsyncSession],
) -> AsyncIterator[PostgresSessionRepository]:
    yield PostgresSessionRepository(pg_factory)


def _record(*, session_id: str | None = None, status: str = "active") -> SessionRecord:
    return SessionRecord(
        session_id=session_id or f"ses_{uuid.uuid4().hex[:16]}",
        user_id="u_pg_test",
        mode="sandbox",
        scenario_id="sc_001",
        persona_id="p_hard",
        user_goal="保住周末",
        status=status,
        created_at=datetime.now(UTC),
    )


async def _cleanup(
    factory: async_sessionmaker[AsyncSession], session_ids: list[str]
) -> None:
    async with factory() as session, session.begin():
        await session.execute(delete(SessionRow).where(SessionRow.session_id.in_(session_ids)))


@pytest.mark.integration
async def test_save_then_get_round_trips(
    repo: PostgresSessionRepository,
    pg_factory: async_sessionmaker[AsyncSession],
) -> None:
    record = _record()
    try:
        await repo.save(record)

        loaded = await repo.get(record.session_id)
        assert loaded is not None
        assert loaded.session_id == record.session_id
        assert loaded.scenario_id == "sc_001"
        assert loaded.status == "active"
        assert loaded.ended_at is None
    finally:
        await _cleanup(pg_factory, [record.session_id])


@pytest.mark.integration
async def test_get_unknown_session_returns_none(repo: PostgresSessionRepository) -> None:
    assert await repo.get("ses_nonexistent_abc") is None


@pytest.mark.integration
async def test_mark_ended_flips_status_and_stamps_time(
    repo: PostgresSessionRepository,
    pg_factory: async_sessionmaker[AsyncSession],
) -> None:
    record = _record()
    ended_at = datetime.now(UTC) + timedelta(minutes=5)
    try:
        await repo.save(record)

        updated = await repo.mark_ended(record.session_id, ended_at=ended_at)
        assert updated.status == "ended"
        assert updated.ended_at is not None
        # Postgres stores tz-aware; compare with second precision so
        # microsecond drift across connections doesn't make this flaky.
        assert abs((updated.ended_at - ended_at).total_seconds()) < 1.0

        # Re-read to confirm the row actually got persisted, not just
        # the in-memory model returned by the same call.
        reread = await repo.get(record.session_id)
        assert reread is not None
        assert reread.status == "ended"
    finally:
        await _cleanup(pg_factory, [record.session_id])


@pytest.mark.integration
async def test_mark_ended_unknown_session_raises_key_error(
    repo: PostgresSessionRepository,
) -> None:
    with pytest.raises(KeyError):
        await repo.mark_ended("ses_nonexistent_xyz", ended_at=datetime.now(UTC))


@pytest.mark.integration
async def test_save_is_idempotent_for_same_id(
    repo: PostgresSessionRepository,
    pg_factory: async_sessionmaker[AsyncSession],
) -> None:
    """The protocol contract is upsert-on-PK; double-save with the same
    id must update rather than 500 on conflict."""
    record = _record()
    try:
        await repo.save(record)
        # Second save with the same id but a changed scenario.
        updated = SessionRecord(
            session_id=record.session_id,
            user_id=record.user_id,
            mode=record.mode,
            scenario_id="sc_002",
            persona_id=record.persona_id,
            user_goal=record.user_goal,
            status=record.status,
            created_at=record.created_at,
        )
        await repo.save(updated)

        loaded = await repo.get(record.session_id)
        assert loaded is not None
        assert loaded.scenario_id == "sc_002"
    finally:
        await _cleanup(pg_factory, [record.session_id])
