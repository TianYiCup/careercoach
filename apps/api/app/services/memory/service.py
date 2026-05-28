"""Memory service — long-term episodic recall (Character Engine L6).

`record_safe` stores how a finished session went (best-effort so a
memory-store hiccup never fails session end). `recall` reads the
opponent's memory of a (user, scenario) for injection into the roleplay
prompt, and `build_memory_note` renders that into the Chinese stage
direction the opponent reads.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import structlog

from app.services.memory.repository import EpisodeRecord, EpisodeRepository

logger = structlog.get_logger(__name__)

# Outcome verdict → Chinese gloss for the memory note.
_RESULT_GLOSS = {
    "shenfeng": "你赢得漂亮",
    "guolu": "打成平手、没占到便宜",
    "fanche": "你没拿下、翻了车",
}


class MemoryService:
    """Owns the opponent's per-(user, scenario) episodic memory."""

    def __init__(self, *, repo: EpisodeRepository) -> None:
        self._repo = repo

    async def record_safe(
        self,
        *,
        user_id: str,
        scenario_id: str,
        result: str,
        takeaway: str,
        now: datetime | None = None,
    ) -> None:
        """Best-effort record of one finished session. Swallows + logs any
        error so a memory-store hiccup never fails session end (mirrors
        the streak `touch_safe` / profile `record_safe` contract)."""
        try:
            await self._repo.record(
                user_id=user_id,
                scenario_id=scenario_id,
                result=result,
                takeaway=takeaway[:300],
                now=now or datetime.now(UTC),
                fresh_id=f"ep_{uuid.uuid4().hex[:8]}",
            )
        except Exception:
            logger.exception("memory_record_failed", user_id=user_id, scenario_id=scenario_id)

    async def recall(self, *, user_id: str, scenario_id: str) -> EpisodeRecord | None:
        """The opponent's memory of this user in this scenario, or None.

        Best-effort — on any repo error we return None so a memory outage
        degrades to "no memory" rather than breaking the turn / create."""
        try:
            return await self._repo.get(user_id=user_id, scenario_id=scenario_id)
        except Exception:
            logger.exception("memory_recall_failed", user_id=user_id, scenario_id=scenario_id)
            return None


def build_memory_note(episode: EpisodeRecord | None) -> str:
    """Render the recalled episode into a stage direction for the roleplay
    prompt. Empty string when there's no memory (first visit) so the
    prompt collapses to its pre-L6 shape.

    Phrased as "you vaguely remember this person" rather than a verbatim
    recap, so the opponent surfaces it naturally instead of robotically
    reciting last session."""
    if episode is None or episode.visit_count < 1:
        return ""
    outcome = _RESULT_GLOSS.get(episode.last_result, "")
    takeaway = episode.last_takeaway.strip()
    parts = [
        f"你之前和这个用户在这个场景交手过 {episode.visit_count} 次，你隐约记得他。",
    ]
    if outcome:
        parts.append(f"上次{outcome}。")
    if takeaway:
        parts.append(f"上次他的主要问题：{takeaway}")
    parts.append("可以在合适的时机不经意地点破你记得他、记得上次，但别生硬复述，要像真人那样自然。")
    return "".join(parts)


__all__ = ["MemoryService", "build_memory_note"]
