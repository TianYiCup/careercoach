"""User lookup + upsert for the SMS auth flow.

`/verify` calls `get_or_create_by_phone(phone)` once per successful
code match. The DB-backed PR drops a SQLAlchemy implementation into
the same Protocol slot.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol, runtime_checkable


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
            user_id=f"u_{uuid.uuid4()}",
            phone=phone,
            nickname=nickname,
            persona_type=persona_type,
            is_minor=is_minor,
            created_at=datetime.now(UTC),
        )
        self._by_phone[phone] = record
        return record


__all__ = ["InMemoryUserRepository", "UserRecord", "UserRepository"]
