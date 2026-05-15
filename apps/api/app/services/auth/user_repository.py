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
from dataclasses import dataclass, replace
from datetime import UTC, date, datetime
from typing import Protocol, runtime_checkable

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.models.user import PersonaType, User

_USER_ID_PREFIX = "u_"


@dataclass(frozen=True)
class UserRecord:
    """The fields the auth service needs to mint a token and return
    the public profile. Distinct from `app.models.user.User` so the
    in-memory impl doesn't depend on the SQLAlchemy session.

    `birthdate` is the source-of-truth for the minor gate; `is_minor`
    is a denormalised cache so JWT mints don't recompute on every
    request. Both are updated together by `update_birthdate`.
    """

    user_id: str
    phone: str
    nickname: str
    persona_type: str  # in_school | intern | graduate
    is_minor: bool
    birthdate: date | None
    created_at: datetime


@runtime_checkable
class UserRepository(Protocol):
    """Persistence seam — SQLAlchemy-backed impl lands later."""

    async def get_by_phone(self, phone: str) -> UserRecord | None: ...

    async def get_by_user_id(self, user_id: str) -> UserRecord | None:
        """Look up by wire-form `u_<uuid>` id (the JWT `sub` claim).
        Returns None if no such user; the route layer maps that to 404.
        """

    async def create(
        self,
        *,
        phone: str,
        nickname: str,
        persona_type: str,
        is_minor: bool,
    ) -> UserRecord: ...

    async def update_birthdate(
        self,
        user_id: str,
        *,
        birthdate: date,
        is_minor: bool,
    ) -> UserRecord:
        """Atomically write `birthdate` + recomputed `is_minor`. The
        caller (AuthService) computes `is_minor` from `birthdate` so
        the storage layer doesn't have to know the policy threshold.
        Raises `KeyError` if `user_id` doesn't exist — the route maps
        that to 404.
        """


class InMemoryUserRepository:
    """Dict-backed store. Not thread-safe; single-worker assumption."""

    def __init__(self) -> None:
        self._by_phone: dict[str, UserRecord] = {}

    async def get_by_phone(self, phone: str) -> UserRecord | None:
        return self._by_phone.get(phone)

    async def get_by_user_id(self, user_id: str) -> UserRecord | None:
        for record in self._by_phone.values():
            if record.user_id == user_id:
                return record
        return None

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
            birthdate=None,
            created_at=datetime.now(UTC),
        )
        self._by_phone[phone] = record
        return record

    async def update_birthdate(
        self,
        user_id: str,
        *,
        birthdate: date,
        is_minor: bool,
    ) -> UserRecord:
        for phone, record in self._by_phone.items():
            if record.user_id == user_id:
                updated = replace(record, birthdate=birthdate, is_minor=is_minor)
                self._by_phone[phone] = updated
                return updated
        raise KeyError(user_id)


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

    async def get_by_user_id(self, user_id: str) -> UserRecord | None:
        try:
            pk = _user_id_to_uuid(user_id)
        except ValueError:
            # Malformed user_id — treat as not found; never raises 500.
            return None
        async with self._session_factory() as session:
            row = await session.get(User, pk)
            return _model_to_record(row) if row is not None else None

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

    async def update_birthdate(
        self,
        user_id: str,
        *,
        birthdate: date,
        is_minor: bool,
    ) -> UserRecord:
        try:
            pk = _user_id_to_uuid(user_id)
        except ValueError as exc:
            raise KeyError(user_id) from exc
        async with self._session_factory() as session, session.begin():
            row = await session.get(User, pk)
            if row is None:
                raise KeyError(user_id)
            row.birthdate = birthdate
            row.is_minor = is_minor
            await session.flush()
            return _model_to_record(row)


def _model_to_record(row: User) -> UserRecord:
    return UserRecord(
        user_id=_uuid_to_user_id(row.id),
        phone=row.phone,
        nickname=row.nickname,
        persona_type=row.persona_type.value,
        is_minor=row.is_minor,
        birthdate=row.birthdate,
        created_at=row.created_at,
    )


def _uuid_to_user_id(value: uuid.UUID) -> str:
    return f"{_USER_ID_PREFIX}{value}"


def _user_id_to_uuid(user_id: str) -> uuid.UUID:
    if not user_id.startswith(_USER_ID_PREFIX):
        raise ValueError(f"user_id must start with {_USER_ID_PREFIX!r}")
    return uuid.UUID(user_id[len(_USER_ID_PREFIX) :])


__all__ = [
    "InMemoryUserRepository",
    "PostgresUserRepository",
    "UserRecord",
    "UserRepository",
]
