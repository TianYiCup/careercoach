"""Opponent persona service package — PRD §7.3 / US-A2.

The catalog is 4 fixed records (PRD §10.2), so there is no repository
or backend swap here — just the in-Python catalog and its accessors.

Public surface:
  * `PersonaRecord` — internal record, carries the hidden `system_prompt`
  * `list_personas()` — all 4, ordered easy → hard
  * `get_persona(persona_id)` — lookup by id, `None` if unknown
"""

from app.services.personas.catalog import (
    PersonaRecord,
    get_persona,
    list_personas,
)

__all__ = ["PersonaRecord", "get_persona", "list_personas"]
