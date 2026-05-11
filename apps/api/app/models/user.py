"""User entity (PRD §6.1)."""

import uuid
from datetime import date, datetime
from enum import StrEnum

from sqlalchemy import Boolean, Date, DateTime, Enum, String, func
from sqlalchemy.dialects.postgresql import UUID as PgUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class PersonaType(StrEnum):
    """Identity-driven coaching tone (PRD §3.0.5 E)."""

    in_school = "in_school"
    intern = "intern"
    graduate = "graduate"


class User(Base):
    """Phone-first auth, with identity flags that gate minor mode and tone."""

    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    phone: Mapped[str] = mapped_column(String(20), unique=True, nullable=False)
    nickname: Mapped[str] = mapped_column(String(64), nullable=False)
    birthdate: Mapped[date | None] = mapped_column(Date, nullable=True)
    is_minor: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
        server_default="false",
    )
    persona_type: Mapped[PersonaType] = mapped_column(
        Enum(
            PersonaType,
            name="persona_type",
            native_enum=True,
            create_constraint=True,
            validate_strings=True,
        ),
        nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    def __repr__(self) -> str:
        return f"User(id={self.id!s}, persona_type={self.persona_type.value})"
