"""Integration smoke for `PostgresStreakRepository`.

Skipped automatically when postgres isn't reachable. Builds its own
engine per test (see `test_sessions_postgres_repository.py` for why)
and cleans up its own rows — `streaks` has no FK to cascade from.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from datetime import date

import pytest
import pytest_asyncio
from app.config import get_settings
from app.models import Streak
from app.services.streak.repository import PostgresStreakRepository, StreakRecord
from sqlalchemy import delete, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

# Shared prefix so per-test cleanup can sweep every row this module wrote.
_USER_PREFIX = "u_streak_pg_"


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
) -> AsyncIterator[PostgresStreakRepository]:
    yield PostgresStreakRepository(pg_factory)
    async with pg_factory() as session, session.begin():
        await session.execute(delete(Streak).where(Streak.user_id.like(f"{_USER_PREFIX}%")))


def _uid() -> str:
    return f"{_USER_PREFIX}{uuid.uuid4().hex[:8]}"


async def test_get_returns_none_when_absent(repo: PostgresStreakRepository) -> None:
    assert await repo.get(_uid()) is None


async def test_upsert_insert_then_get_roundtrips(repo: PostgresStreakRepository) -> None:
    uid = _uid()
    rec = StreakRecord(
        user_id=uid,
        current_days=5,
        max_days=9,
        last_active_date=date(2026, 5, 20),
    )
    await repo.upsert(rec)
    assert await repo.get(uid) == rec


async def test_upsert_updates_existing_row(repo: PostgresStreakRepository) -> None:
    """A second upsert on the same user_id PK updates in place rather
    than tripping a primary-key violation."""
    uid = _uid()
    await repo.upsert(
        StreakRecord(user_id=uid, current_days=3, max_days=3, last_active_date=date(2026, 5, 20))
    )
    await repo.upsert(
        StreakRecord(user_id=uid, current_days=4, max_days=4, last_active_date=date(2026, 5, 21))
    )
    got = await repo.get(uid)
    assert got is not None
    assert got.current_days == 4
    assert got.max_days == 4
    assert got.last_active_date == date(2026, 5, 21)
