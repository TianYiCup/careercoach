"""ORM models. Importing this package registers every model on Base.metadata.

alembic/env.py imports `app.models` so autogenerate sees all tables.
"""

from app.models.user import PersonaType, User

__all__ = ["PersonaType", "User"]
