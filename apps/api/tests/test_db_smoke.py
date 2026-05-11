"""Integration smoke test: insert + query a User against docker postgres.

Skipped automatically when the DB isn't reachable, so this file is safe to run
in environments without docker compose. CI must boot postgres before pytest
to actually exercise the assertion.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator

import pytest
import pytest_asyncio
from app.config import get_settings
from app.db import async_session_factory, engine
from app.models import PersonaType, User
from sqlalchemy import select, text


@pytest_asyncio.fixture
async def db_available() -> AsyncIterator[bool]:
    """Yield True iff the configured postgres is reachable."""
    try:
        async with engine.connect() as conn:
            await conn.execute(text("select 1"))
        yield True
    except Exception as exc:
        pytest.skip(f"postgres unreachable at {get_settings().database_url}: {exc}")


@pytest.mark.integration
async def test_insert_and_query_user(db_available: bool) -> None:
    assert db_available
    phone = f"139{uuid.uuid4().int % 10**8:08d}"

    async with async_session_factory() as session, session.begin():
        user = User(
            phone=phone,
            nickname="测试同学",
            persona_type=PersonaType.in_school,
        )
        session.add(user)

    async with async_session_factory() as session:
        result = await session.execute(select(User).where(User.phone == phone))
        loaded = result.scalar_one()

        assert loaded.id is not None
        assert loaded.persona_type is PersonaType.in_school
        assert loaded.is_minor is False
        assert loaded.created_at is not None

        await session.delete(loaded)
        await session.commit()
