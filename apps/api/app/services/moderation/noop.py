"""A backend that allows every input — used until PR ② lands the dict.

Wiring `/v1/moderation/check` to a Noop backend in PR ① is intentional:
it lets us validate the whole pipeline (route → service → audit sink)
end-to-end without committing to the keyword list yet. The route will
return `verdict=allow` for any content, which the contract tests can
already assert against.
"""

from __future__ import annotations

from app.schemas.moderation import ModerationContext
from app.services.moderation.types import Decision


class NoopBackend:
    """Always returns `verdict=allow`. Structurally implements `ModerationBackend`."""

    name: str = "noop"

    async def evaluate(self, content: str, context: ModerationContext) -> Decision:
        return Decision(verdict="allow", score=0.0)
