"""Integration smoke for `PostgresVibeRepository`.

Skipped automatically when postgres isn't reachable. Builds its own
engine per test (see `test_sessions_postgres_repository.py` for why)
and cleans up its own rows — `vibe_logs` has no FK to cascade from.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from datetime import UTC, date, datetime

import pytest
import pytest_asyncio
from app.config import get_settings
from app.models import VibeLog
from app.services.vibe.repository import PostgresVibeRepository, VibeLogRecord, VibeType
from sqlalchemy import delete, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

# Shared prefix so the per-test cleanup can sweep every row this module
# wrote, regardless of which test created it.
_USER_PREFIX = "u_vibe_pg_"


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
async def repo(
    pg_factory: async_sessionmaker[AsyncSession],
) -> AsyncIterator[PostgresVibeRepository]:
    yield PostgresVibeRepository(pg_factory)
    async with pg_factory() as session, session.begin():
        await session.execute(delete(VibeLog).where(VibeLog.user_id.like(f"{_USER_PREFIX}%")))


def _record(*, user_id: str, vibe: VibeType = "fire", day: date | None = None) -> VibeLogRecord:
    return VibeLogRecord(
        id=f"vibe_{uuid.uuid4().hex[:8]}",
        user_id=user_id,
        vibe=vibe,
        logged_date=day or date(2026, 5, 20),
        created_at=datetime.now(UTC),
    )


def _uid() -> str:
    return f"{_USER_PREFIX}{uuid.uuid4().hex[:8]}"


async def test_set_today_then_get_roundtrips(repo: PostgresVibeRepository) -> None:
    uid = _uid()
    await repo.set_today(_record(user_id=uid, vibe="excited"))
    got = await repo.get_for_date(uid, date(2026, 5, 20))
    assert got is not None
    assert got.user_id == uid
    assert got.vibe == "excited"


async def test_get_for_date_returns_none_when_absent(repo: PostgresVibeRepository) -> None:
    assert await repo.get_for_date(_uid(), date(2026, 5, 20)) is None


async def test_set_today_recheckin_overwrites_keeps_id(repo: PostgresVibeRepository) -> None:
    """A second check-in the same day overwrites the mood without
    tripping the (user_id, logged_date) unique constraint, and keeps
    the original row id."""
    uid = _uid()
    first = _record(user_id=uid, vibe="fire")
    await repo.set_today(first)
    await repo.set_today(_record(user_id=uid, vibe="meh"))
    got = await repo.get_for_date(uid, date(2026, 5, 20))
    assert got is not None
    assert got.vibe == "meh"
    assert got.id == first.id  # original id retained on overwrite
