"""Scenario catalog row (PRD §7.3).

One row per practice scenario the user can pick from on the home
screen. v0 reads from `app.services.scenarios.seed_data` (in-Python
constants) so this model + the alembic migration ship for parity with
`Session` but aren't wired to the request path yet — the swap is the
job of a later PR.

Schema follows `ScenarioSummary` in `app/schemas/scenarios.py` plus
the seed fields (`persona_title`, `opening_line`) that the session
service needs at create + end time. Co-locating them avoids a join
when sessions create needs both halves of a scenario.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import JSON, Boolean, DateTime, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class Scenario(Base):
    """One practice scenario the user can launch into a sandbox session."""

    __tablename__ = "scenarios"

    # `sc_<3-digit>` for the seeded catalog; a Postgres-backed extension
    # might use a longer slug, so 64 chars covers both.
    id: Mapped[str] = mapped_column(String(64), primary_key=True)

    title: Mapped[str] = mapped_column(String(64), nullable=False)

    # Plain text instead of a typed Enum so adding a category (e.g.
    # `internship`) doesn't need an ALTER TYPE migration. Pydantic
    # `ScenarioCategory` literal enforces the closed set at the API
    # boundary.
    category: Mapped[str] = mapped_column(String(16), nullable=False, index=True)

    difficulty: Mapped[int] = mapped_column(Integer, nullable=False)

    # JSONB on Postgres, JSON1 on SQLite — both store the list shape.
    tags: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)

    background: Mapped[str] = mapped_column(String(500), nullable=False)

    real_user_certified: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        server_default="false",
    )

    # Seed fields colocated so session create doesn't join.
    persona_title: Mapped[str] = mapped_column(String(64), nullable=False)
    opening_line: Mapped[str] = mapped_column(String(300), nullable=False)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    def __repr__(self) -> str:
        return f"Scenario(id={self.id}, title={self.title})"
