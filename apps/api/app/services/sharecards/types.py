"""Renderer input payload — decoupled from the Pydantic `Score` schema.

PR ③'s service layer pulls a `Score` out of the DB and converts it
into `SessionCardData`. Keeping these decoupled means schema iteration
(OpenAPI examples, response shape) doesn't ripple into the renderer.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.schemas.sessions import ScoreResult

_DIMENSION_NAMES: tuple[tuple[str, str], ...] = (
    ("aura", "气场"),
    ("logic", "逻辑"),
    ("emotion", "情绪"),
    ("professionalism", "专业"),
    ("goal_achieve", "达成"),
)
"""Internal score dimension keys → display labels used on the card."""


@dataclass(frozen=True)
class SessionCardData:
    """Payload the session-card renderer turns into a 1080×1920 PNG.

    Range invariants on the five score dimensions are enforced here so
    the renderer can trust its input and skip defensive bounds-checking.
    `user_caption` is moderated by the service layer (PRD §7.8) BEFORE
    being baked into a card — the renderer takes the post-moderation
    string at face value.
    """

    scenario_title: str
    persona_title: str
    aura: int
    logic: int
    emotion: int
    professionalism: int
    goal_achieve: int
    result: ScoreResult
    highlights: str
    user_caption: str | None = None

    def __post_init__(self) -> None:
        for key, _label in _DIMENSION_NAMES:
            value = getattr(self, key)
            if not 0 <= value <= 10:
                raise ValueError(f"{key} out of range [0, 10]: {value}")
        if not self.scenario_title:
            raise ValueError("scenario_title must not be empty")
        if not self.persona_title:
            raise ValueError("persona_title must not be empty")

    @property
    def overall_score(self) -> float:
        """One-decimal aggregate over the five dimensions — what the big number on the card shows."""
        total = self.aura + self.logic + self.emotion + self.professionalism + self.goal_achieve
        return round(total / len(_DIMENSION_NAMES), 1)

    @property
    def top_dimensions(self) -> tuple[tuple[str, int], ...]:
        """Top 3 dimensions by score, ordered descending. Drives the StickerBadge row."""
        scored = [(label, getattr(self, key)) for key, label in _DIMENSION_NAMES]
        scored.sort(key=lambda kv: -kv[1])
        return tuple(scored[:3])
