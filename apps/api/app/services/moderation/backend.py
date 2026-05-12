"""`ModerationBackend` Protocol — the only contract the service sees.

Concrete backends (local dict, Alibaba Cloud Content Safety, cascading)
implement this structurally. Following the LLM provider pattern
(foundation §3.3.4) we keep the surface minimal: one async method,
typed errors, no DB / HTTP awareness.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from app.schemas.moderation import ModerationContext
from app.services.moderation.types import Decision


@runtime_checkable
class ModerationBackend(Protocol):
    """Score raw content for safety.

    Implementations MUST:
      * be safe to call concurrently
      * never raise on routine "content is unsafe" outcomes — those are
        Decisions, not errors
      * raise `ModerationBackendError` for upstream failures (timeout,
        auth, 5xx) so the cascading backend can fall back
    """

    name: str
    """Stable identifier used in audit rows and logs (e.g. "local_dict")."""

    async def evaluate(self, content: str, context: ModerationContext) -> Decision:
        """Score `content` in the given `context`.

        Args:
            content: raw text from a user or LLM; the backend MUST NOT
                pre-trim or assume normalisation.
            context: where the content came from — drives different
                sensitivity tables in dict backends and different
                scenes in cloud backends.
        """
        ...


class ModerationBackendError(Exception):
    """Raised by a backend when scoring failed for an upstream reason.

    A `CascadingBackend` (PR ③) catches this to fall through to the
    next backend; in PR ① the service simply propagates it up as a 502
    via the route layer.
    """

    def __init__(self, message: str, *, backend: str) -> None:
        super().__init__(message)
        self.backend = backend
