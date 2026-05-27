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

    Either `phone` or `email` is set (but not necessarily both — a
    user that verified via the email path has `phone=None` until they
    optionally bind a phone later). PR-A4 (Postgres persistence) folds
    the email column into the DB schema; until then the email branch
    only works against `InMemoryUserRepository`.
    """

    user_id: str
    phone: str | None
    nickname: str
    persona_type: str  # in_school | intern | graduate
    is_minor: bool
    birthdate: date | None
    created_at: datetime
    email: str | None = None


@runtime_checkable
class UserRepository(Protocol):
    """Persistence seam — SQLAlchemy-backed impl lands later."""

    async def get_by_phone(self, phone: str) -> UserRecord | None: ...

    async def get_by_email(self, email: str) -> UserRecord | None:
        """Lookup-by-email mirror of `get_by_phone` for the email
        auth path (PR-A2). PG impl is a stub until PR-A4 adds the
        `email` column to the users table."""

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

    async def create_email_user(
        self,
        *,
        email: str,
        nickname: str,
        persona_type: str,
        is_minor: bool,
    ) -> UserRecord:
        """Create a user whose primary identifier is `email`, not
        `phone`. PR-A4 (Postgres persistence) makes this work against
        the PG-backed impl; until then only `InMemoryUserRepository`
        supports email users."""

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
    """Dict-backed store. Not thread-safe; single-worker assumption.

    Holds both phone-keyed and email-keyed users in a single dict
    keyed by `user_id`, with two secondary indexes for `get_by_phone`
    and `get_by_email`. Either index can be empty for a given record."""

    def __init__(self) -> None:
        self._by_user_id: dict[str, UserRecord] = {}
        self._user_id_by_phone: dict[str, str] = {}
        self._user_id_by_email: dict[str, str] = {}

    async def get_by_phone(self, phone: str) -> UserRecord | None:
        user_id = self._user_id_by_phone.get(phone)
        if user_id is None:
            return None
        return self._by_user_id.get(user_id)

    async def get_by_email(self, email: str) -> UserRecord | None:
        user_id = self._user_id_by_email.get(email.lower())
        if user_id is None:
            return None
        return self._by_user_id.get(user_id)

    async def get_by_user_id(self, user_id: str) -> UserRecord | None:
        return self._by_user_id.get(user_id)

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
        self._by_user_id[record.user_id] = record
        self._user_id_by_phone[phone] = record.user_id
        return record

    async def create_email_user(
        self,
        *,
        email: str,
        nickname: str,
        persona_type: str,
        is_minor: bool,
    ) -> UserRecord:
        normalised = email.lower()
        record = UserRecord(
            user_id=f"{_USER_ID_PREFIX}{uuid.uuid4()}",
            phone=None,
            nickname=nickname,
            persona_type=persona_type,
            is_minor=is_minor,
            birthdate=None,
            created_at=datetime.now(UTC),
            email=normalised,
        )
        self._by_user_id[record.user_id] = record
        self._user_id_by_email[normalised] = record.user_id
        return record

    async def update_birthdate(
        self,
        user_id: str,
        *,
        birthdate: date,
        is_minor: bool,
    ) -> UserRecord:
        existing = self._by_user_id.get(user_id)
        if existing is None:
            raise KeyError(user_id)
        updated = replace(existing, birthdate=birthdate, is_minor=is_minor)
        self._by_user_id[user_id] = updated
        return updated


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

    async def get_by_email(self, email: str) -> UserRecord | None:
        # PR-A2 — the PG users table doesn't yet carry an `email`
        # column. PR-A4 (Postgres persistence) adds it; until then we
        # return None so the email auth path falls through to "user
        # not found" and the wiring layer keeps the email backend on
        # InMemoryUserRepository regardless of `auth_repo_backend`.
        return None

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

    async def create_email_user(
        self,
        *,
        email: str,
        nickname: str,
        persona_type: str,
        is_minor: bool,
    ) -> UserRecord:
        # PR-A2 — see `get_by_email` above. The PG schema does not yet
        # carry the email column, so creating an email-only user
        # against PG is structurally impossible. The wiring layer
        # routes email auth through InMemoryUserRepository even when
        # `auth_repo_backend=postgres`; PR-A4 will collapse that.
        raise NotImplementedError(
            "PostgresUserRepository.create_email_user is deferred until "
            "PR-A4 adds the email column. Use InMemoryUserRepository "
            "for the email auth path in the meantime."
        )


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
