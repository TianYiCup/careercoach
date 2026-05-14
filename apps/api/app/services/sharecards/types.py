"""Renderer input payloads — decoupled from the Pydantic `Score` schema.

The service layer pulls per-session, per-week, or per-year data out of
the score repository and converts it into the dataclasses defined here.
Keeping these decoupled means schema iteration (OpenAPI examples,
response shape) doesn't ripple into the renderer.

Three types, one per ShareCardType:
  * `SessionCardData`  — single sandbox scorecard
  * `WeeklyCardData`   — weekly digest (Mon-Sun, Asia/Shanghai)
  * `WrappedCardData`  — annual six-page recap
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


@dataclass(frozen=True)
class WeeklyCardData:
    """Payload for the weekly digest renderer.

    Aggregated by `ShareCardService.create_weekly_card` from sessions
    that landed inside the requested Mon-Sun window (Asia/Shanghai).
    An empty week is represented by `sessions_count=0`; the renderer
    swaps to its "no practice" copy rather than producing a blank
    number.
    """

    week_label: str  # e.g. "2026 第 20 周"
    sessions_count: int
    shenfeng_count: int
    guolu_count: int
    fanche_count: int
    top_scenario_title: str | None  # None when sessions_count == 0
    headline: str  # K's one-liner — service composes this from the bucket

    def __post_init__(self) -> None:
        if self.sessions_count < 0:
            raise ValueError(f"sessions_count must be ≥ 0: {self.sessions_count}")
        # The three verdict buckets together can exceed sessions_count when
        # multi-turn rollups land later, so they're loose — but each must be
        # individually non-negative.
        for label, value in (
            ("shenfeng_count", self.shenfeng_count),
            ("guolu_count", self.guolu_count),
            ("fanche_count", self.fanche_count),
        ):
            if value < 0:
                raise ValueError(f"{label} must be ≥ 0: {value}")
        if not self.week_label:
            raise ValueError("week_label must not be empty")
        if not self.headline:
            raise ValueError("headline must not be empty")


@dataclass(frozen=True)
class WrappedCardData:
    """Payload for the annual 6-page Wrapped recap.

    Each field maps to one page in design-spec §10.3 order. Empty-year
    cases (no sessions at all) still produce a valid render — the
    renderer flips to "you'll be back" copy keyed off `total_sessions==0`.
    """

    year: int
    total_sessions: int
    top_scenario_title: str  # page 2 — most-practiced
    top_opponent_title: str  # page 3 — most-faced persona
    best_session_title: str  # page 4 — top shenfeng
    worst_session_title: str  # page 5 — worst fanche
    closing_letter: str  # page 6 — K's outro

    def __post_init__(self) -> None:
        if self.year < 2024 or self.year > 2099:
            raise ValueError(f"year out of range [2024, 2099]: {self.year}")
        if self.total_sessions < 0:
            raise ValueError(f"total_sessions must be ≥ 0: {self.total_sessions}")
        if not self.closing_letter:
            raise ValueError("closing_letter must not be empty")
