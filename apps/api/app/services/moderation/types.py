"""Internal types for the moderation service.

`Decision` is the backend's output — what verdict, which categories,
how confident. The service maps it into the public
`ModerationCheckResponse` and persists a parallel `ModerationEvent`
audit row. Backends never touch the HTTP schema or the DB; that keeps
backend implementations small and swappable.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from app.schemas.moderation import (
    ModerationCategory,
    ModerationVerdict,
    RedirectResource,
)


@dataclass(frozen=True)
class Decision:
    """A backend's scoring result for a single piece of content.

    Frozen so backends can hand the same object to multiple sinks (the
    public response + the audit log) without one mutating the other.
    """

    verdict: ModerationVerdict
    score: float
    categories: tuple[ModerationCategory, ...] = field(default_factory=tuple)
    redirect_resource: RedirectResource | None = None

    def __post_init__(self) -> None:
        if not 0.0 <= self.score <= 1.0:
            raise ValueError(f"score must be in [0, 1], got {self.score}")
        if self.verdict == "redirect" and self.redirect_resource is None:
            raise ValueError("verdict='redirect' requires a redirect_resource")
        if self.verdict == "allow" and self.categories:
            raise ValueError("verdict='allow' must not list categories")
