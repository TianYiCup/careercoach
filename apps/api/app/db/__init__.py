"""Database access layer.

`Base` is the single declarative base every ORM model must inherit. Keep
metadata in `Base.metadata` so alembic autogenerate has one source of truth.
"""

from app.db.base import Base
from app.db.session import async_session_factory, engine

__all__ = ["Base", "async_session_factory", "engine"]
