"""Integration smoke for `PostgresUserRepository`.

Skipped automatically when postgres isn't reachable. Each test cleans
up the `users` rows it inserts so re-running on a real DB doesn't
accumulate row debt across runs.

Engine-per-test fixture mirrors the pattern in
`test_sessions_postgres_repository.py` — the module-level engine in
`app.db.session` is born inside one event loop and asyncpg's pool
becomes unusable from later tests' loops.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator

import pytest
import pytest_asyncio
from app.config import get_settings
from app.models import User
from app.services.auth.user_repository import PostgresUserRepository
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
async def repo(
    pg_factory: async_sessionmaker[AsyncSession],
) -> AsyncIterator[PostgresUserRepository]:
    yield PostgresUserRepository(pg_factory)


def _unique_phone() -> str:
    """Mainland-format phone (`1[3-9]\\d{9}`) that's almost certainly
    not in the DB yet — uses uuid entropy to avoid collisions across
    parallel CI shards."""
    suffix = uuid.uuid4().int % 10**8
    return f"139{suffix:08d}"


async def _cleanup(factory: async_sessionmaker[AsyncSession], phones: list[str]) -> None:
    async with factory() as session, session.begin():
        await session.execute(delete(User).where(User.phone.in_(phones)))


@pytest.mark.integration
async def test_create_then_get_round_trips(
    repo: PostgresUserRepository,
    pg_factory: async_sessionmaker[AsyncSession],
) -> None:
    phone = _unique_phone()
    try:
        created = await repo.create(
            phone=phone,
            nickname="测试同学",
            persona_type="in_school",
            is_minor=False,
        )

        assert created.user_id.startswith("u_")
        assert created.phone == phone
        assert created.persona_type == "in_school"
        assert created.is_minor is False
        assert created.created_at is not None

        loaded = await repo.get_by_phone(phone)
        assert loaded is not None
        assert loaded.user_id == created.user_id
        assert loaded.nickname == "测试同学"
    finally:
        await _cleanup(pg_factory, [phone])


@pytest.mark.integration
async def test_get_unknown_phone_returns_none(
    repo: PostgresUserRepository,
) -> None:
    # 138-prefix is valid mobile format but the value is unique enough
    # not to collide with anything seeded by other tests.
    assert await repo.get_by_phone(f"138{uuid.uuid4().int % 10**8:08d}") is None


@pytest.mark.integration
async def test_each_persona_type_round_trips(
    repo: PostgresUserRepository,
    pg_factory: async_sessionmaker[AsyncSession],
) -> None:
    """persona_type is a Postgres ENUM via SQLAlchemy `Enum(...)` — make
    sure every literal the API accepts survives the round trip."""
    phones: list[str] = []
    try:
        for persona in ("in_school", "intern", "graduate"):
            phone = _unique_phone()
            phones.append(phone)
            created = await repo.create(
                phone=phone,
                nickname=f"persona-{persona}",
                persona_type=persona,
                is_minor=False,
            )
            assert created.persona_type == persona

            loaded = await repo.get_by_phone(phone)
            assert loaded is not None
            assert loaded.persona_type == persona
    finally:
        await _cleanup(pg_factory, phones)


@pytest.mark.integration
async def test_is_minor_flag_round_trips(
    repo: PostgresUserRepository,
    pg_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Minor mode gates copilot + speech features (PRD §3.0.5 C); if
    this flag silently flips, the red-line check leaks."""
    minor_phone = _unique_phone()
    adult_phone = _unique_phone()
    try:
        minor = await repo.create(
            phone=minor_phone,
            nickname="minor",
            persona_type="in_school",
            is_minor=True,
        )
        adult = await repo.create(
            phone=adult_phone,
            nickname="adult",
            persona_type="graduate",
            is_minor=False,
        )

        assert minor.is_minor is True
        assert adult.is_minor is False

        loaded_minor = await repo.get_by_phone(minor_phone)
        loaded_adult = await repo.get_by_phone(adult_phone)
        assert loaded_minor is not None
        assert loaded_adult is not None
        assert loaded_minor.is_minor is True
        assert loaded_adult.is_minor is False
    finally:
        await _cleanup(pg_factory, [minor_phone, adult_phone])


@pytest.mark.integration
async def test_user_ids_are_unique_across_creates(
    repo: PostgresUserRepository,
    pg_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Two distinct phones must NOT share a user_id — even if the
    server-side UUID generator hiccuped, the assertion catches it."""
    phone_a = _unique_phone()
    phone_b = _unique_phone()
    try:
        a = await repo.create(
            phone=phone_a,
            nickname="a",
            persona_type="in_school",
            is_minor=False,
        )
        b = await repo.create(
            phone=phone_b,
            nickname="b",
            persona_type="in_school",
            is_minor=False,
        )

        assert a.user_id != b.user_id
        assert a.user_id.startswith("u_")
        assert b.user_id.startswith("u_")
    finally:
        await _cleanup(pg_factory, [phone_a, phone_b])
