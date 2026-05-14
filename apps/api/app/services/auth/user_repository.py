"""User lookup + upsert for the SMS auth flow.

`/verify` calls `get_or_create_by_phone(phone)` once per successful
code match. Two implementations: in-memory for dev / tests without
docker, and PostgreSQL-backed for production. The factory in
`__init__.py` picks one based on `settings.auth_repo_backend`.

User-id shape note
------------------
The wire/JWT format is `u_<uuid>` (matching the InMemory generator),
but the SQLAlchemy `User.id` column is a raw `UUID`. The PG impl
converts at the boundary — `_user_id_to_uuid` + `_uuid_to_user_id`
keep that detail private so route handlers and JWT minting stay
working with the prefixed-string form.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol, runtime_checkable

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.models.user import PersonaType, User

_USER_ID_PREFIX = "u_"


@dataclass(frozen=True)
class UserRecord:
    """The fields the auth service needs to mint a token and return
    the public profile. Distinct from `app.models.user.User` so the
    in-memory impl doesn't depend on the SQLAlchemy session."""

    user_id: str
    phone: str
    nickname: str
    persona_type: str  # in_school | intern | graduate
    is_minor: bool
    created_at: datetime


@runtime_checkable
class UserRepository(Protocol):
    """Persistence seam — SQLAlchemy-backed impl lands later."""

    async def get_by_phone(self, phone: str) -> UserRecord | None: ...

    async def create(
        self,
        *,
        phone: str,
        nickname: str,
        persona_type: str,
        is_minor: bool,
    ) -> UserRecord: ...


class InMemoryUserRepository:
    """Dict-backed store. Not thread-safe; single-worker assumption."""

    def __init__(self) -> None:
        self._by_phone: dict[str, UserRecord] = {}

    async def get_by_phone(self, phone: str) -> UserRecord | None:
        return self._by_phone.get(phone)

    async def create(
        self,
        *,
        phone: str,
        nickname: str,
        persona_type: str,
        is_minor: bool,
    ) -> UserRecord:
        record = UserRecord(
            user_id=f"{_USER_ID_PREFIX}{uuid.uuid4()}",
            phone=phone,
            nickname=nickname,
            persona_type=persona_type,
            is_minor=is_minor,
            created_at=datetime.now(UTC),
        )
        self._by_phone[phone] = record
        return record


class PostgresUserRepository:
    """SQLAlchemy-backed user store.

    `get_by_phone` is a single indexed lookup on `users.phone` (unique
    in the model). `create` issues an INSERT with a server-generated
    timestamp; the returned record carries the database's view of the
    row so callers don't have to round-trip again.

    Async session lifetime mirrors the sessions repo: short-lived,
    one per method call. The auth flow is request-scoped so no
    benefit to longer transactions here.
    """

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def get_by_phone(self, phone: str) -> UserRecord | None:
        async with self._session_factory() as session:
            result = await session.execute(select(User).where(User.phone == phone))
            row = result.scalar_one_or_none()
            if row is None:
                return None
            return _model_to_record(row)

    async def create(
        self,
        *,
        phone: str,
        nickname: str,
        persona_type: str,
        is_minor: bool,
    ) -> UserRecord:
        async with self._session_factory() as session, session.begin():
            user = User(
                phone=phone,
                nickname=nickname,
                persona_type=PersonaType(persona_type),
                is_minor=is_minor,
            )
            session.add(user)
            # `flush()` resolves the server-side defaults (uuid PK,
            # created_at via func.now()) without ending the
            # transaction, so we can return the populated row.
            await session.flush()
            return _model_to_record(user)


def _model_to_record(row: User) -> UserRecord:
    return UserRecord(
        user_id=_uuid_to_user_id(row.id),
        phone=row.phone,
        nickname=row.nickname,
        persona_type=row.persona_type.value,
        is_minor=row.is_minor,
        created_at=row.created_at,
    )


def _uuid_to_user_id(value: uuid.UUID) -> str:
    return f"{_USER_ID_PREFIX}{value}"


__all__ = [
    "InMemoryUserRepository",
    "PostgresUserRepository",
    "UserRecord",
    "UserRepository",
]
