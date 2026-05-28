"""Profile service — the user's strategy profile + adaptive intensity (L5).

`record_safe` folds one coach strategy read into the profile (best-effort
so a stats-store hiccup never fails a turn). `adapt_vector` reads the
profile and scales an opponent's base `CharacterVector` for the user;
`get_stats` is the read side behind `GET /v1/users/me/profile`.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime

import structlog

from app.services.profile.intensity import scale_vector
from app.services.profile.repository import ProfileRepository, StrategyStatRecord
from app.services.scenarios.character_vector import CharacterVector

logger = structlog.get_logger(__name__)


@dataclass(frozen=True)
class ProfileSummary:
    """Read-side view behind `GET /v1/users/me/profile`."""

    stats: list[StrategyStatRecord]
    total_observations: int
    overrelied_strategy: str | None


# Mirrors intensity._OVERRELIANCE_MIN_COUNT — a strategy must be used at
# least this often before it counts as a crutch worth punishing.
_OVERRELIANCE_MIN_COUNT = 3


class ProfileService:
    """Owns the per-user strategy profile + the intensity adaptation."""

    def __init__(self, *, repo: ProfileRepository) -> None:
        self._repo = repo

    async def record_safe(
        self,
        *,
        user_id: str,
        strategy: str,
        effect: str,
        now: datetime | None = None,
    ) -> None:
        """Best-effort record of one strategy read. Swallows + logs any
        error so a stats-store hiccup never fails the turn (mirrors the
        streak `touch_safe` / weakness `apply_safe` contract)."""
        try:
            await self._repo.record(
                user_id=user_id,
                strategy=strategy,
                effect=effect,
                now=now or datetime.now(UTC),
                fresh_id=f"us_{uuid.uuid4().hex[:8]}",
            )
        except Exception:
            logger.exception("profile_record_failed", user_id=user_id)

    async def get_stats(self, user_id: str) -> list[StrategyStatRecord]:
        """The user's strategy stats, highest `count` first. Empty for a
        user who has never been read by the coach."""
        return await self._repo.list_for_user(user_id)

    async def get_summary(self, user_id: str) -> ProfileSummary:
        """Stats + the derived experience total + over-relied crutch, in
        one read — the shape `GET /v1/users/me/profile` returns."""
        stats = await self._repo.list_for_user(user_id)
        return ProfileSummary(
            stats=stats,
            total_observations=sum(s.count for s in stats),
            overrelied_strategy=_overrelied_strategy(stats),
        )

    async def adapt_vector(
        self,
        *,
        user_id: str,
        base: CharacterVector,
    ) -> CharacterVector:
        """Scale `base` for the user: softer for beginners, harder on the
        dims that counter their over-relied-but-failing strategy.

        Best-effort — on any repo error we return `base` unchanged so a
        stats outage can't break session create."""
        try:
            stats = await self._repo.list_for_user(user_id)
        except Exception:
            logger.exception("profile_adapt_failed", user_id=user_id)
            return base

        total = sum(s.count for s in stats)
        overrelied = _overrelied_strategy(stats)
        return scale_vector(
            base,
            total_observations=total,
            overrelied_strategy=overrelied,
        )


def _overrelied_strategy(stats: list[StrategyStatRecord]) -> str | None:
    """The strategy the user leans on most while it keeps failing: the
    highest-count strategy whose poor outcomes dominate (poor > good)
    and which clears the min-count floor. Returns None when no strategy
    qualifies (e.g. everything's working, or not enough data)."""
    candidates = [s for s in stats if s.count >= _OVERRELIANCE_MIN_COUNT and s.poor > s.good]
    if not candidates:
        return None
    # Highest count wins — that's the crutch they reach for most.
    return max(candidates, key=lambda s: s.count).strategy


__all__ = ["ProfileService", "ProfileSummary"]
