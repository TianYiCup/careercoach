"""Integration smoke for `PostgresWeaknessRepository`.

Skipped automatically when postgres isn't reachable. Builds its own
engine per test (see `test_sessions_postgres_repository.py` for why)
and cleans up its own rows — `weaknesses` has no FK to cascade from.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime

import pytest
import pytest_asyncio
from app.config import get_settings
from app.models import Weakness
from app.services.weakness.repository import PostgresWeaknessRepository
from sqlalchemy import delete, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

# Shared prefix so per-test cleanup can sweep every row this module wrote.
_USER_PREFIX = "u_weakness_pg_"
_NOW = datetime(2026, 5, 20, 8, 0, tzinfo=UTC)


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
) -> AsyncIterator[PostgresWeaknessRepository]:
    yield PostgresWeaknessRepository(pg_factory)
    async with pg_factory() as session, session.begin():
        await session.execute(delete(Weakness).where(Weakness.user_id.like(f"{_USER_PREFIX}%")))


def _uid() -> str:
    return f"{_USER_PREFIX}{uuid.uuid4().hex[:8]}"


async def test_increment_inserts_then_list_roundtrips(repo: PostgresWeaknessRepository) -> None:
    uid = _uid()
    await repo.increment(user_id=uid, tag="过早让步", delta=2, now=_NOW, fresh_id="wk_pg_a")
    rows = await repo.list_for_user(uid)
    assert len(rows) == 1
    assert rows[0].tag == "过早让步"
    assert rows[0].frequency == 2


async def test_increment_accumulates_without_pk_violation(
    repo: PostgresWeaknessRepository,
) -> None:
    """A second increment on the same (user_id, tag) accumulates in
    place rather than tripping the unique constraint."""
    uid = _uid()
    await repo.increment(user_id=uid, tag="被绕话题", delta=1, now=_NOW, fresh_id="wk_pg_1")
    await repo.increment(user_id=uid, tag="被绕话题", delta=3, now=_NOW, fresh_id="wk_pg_2")
    rows = await repo.list_for_user(uid)
    assert len(rows) == 1
    assert rows[0].frequency == 4


async def test_list_for_user_orders_by_frequency_desc(
    repo: PostgresWeaknessRepository,
) -> None:
    uid = _uid()
    await repo.increment(user_id=uid, tag="过早让步", delta=1, now=_NOW, fresh_id="wk_pg_a")
    await repo.increment(user_id=uid, tag="被绕话题", delta=5, now=_NOW, fresh_id="wk_pg_b")
    rows = await repo.list_for_user(uid)
    assert [r.tag for r in rows] == ["被绕话题", "过早让步"]


async def test_list_for_user_empty_when_absent(repo: PostgresWeaknessRepository) -> None:
    assert await repo.list_for_user(_uid()) == []
