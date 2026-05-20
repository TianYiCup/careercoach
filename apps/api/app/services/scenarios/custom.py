"""Custom scenario creation — PRD §7.3 / US-A1.

`POST /v1/scenarios/custom` turns a free-text description into a
practisable scenario. v1 (R3-4) is the skeleton: a 10/day quota +
moderation gate + a templated placeholder script. R3-5 swaps the
placeholder for real LLM generation and adds output moderation.

The created scenario is registered in-process (`register_custom_
scenario`) so a follow-up `POST /v1/sessions` can practise its id
immediately. Durable storage is a later epic.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime
from zoneinfo import ZoneInfo

import structlog

from app.schemas.moderation import ModerationCheckRequest, ModerationCheckResponse, RedirectResource
from app.services.moderation import ModerationBackendError
from app.services.moderation.service import ModerationService
from app.services.scenarios.seed_data import ScenarioRecord, register_custom_scenario

logger = structlog.get_logger(__name__)

# PRD §7.12 — 10 custom scenarios per user per day. The quota guards
# the (R3-5) LLM generation cost; a blocked description never reaches
# the generator, so it does not consume quota.
_DAILY_QUOTA = 10

# A streak/vibe-style Asia/Shanghai day boundary (CLAUDE.md §6) — the
# quota resets on the user's local midnight, not UTC's.
_SHANGHAI_TZ = ZoneInfo("Asia/Shanghai")

_TITLE_MAX_CHARS = 20


class CustomScenarioRateLimitedError(RuntimeError):
    """Route maps to 429 — caller hit the daily custom-scenario quota."""

    def __init__(self, *, limit: int) -> None:
        super().__init__(f"daily custom-scenario quota of {limit} reached")
        self.limit = limit


class CustomScenarioBlockedError(RuntimeError):
    """Route maps to 400 — moderation rejected the description (§3.0.5 D:
    自定义场景必过审). Carries the crisis resource when the description
    tripped a `redirect` verdict (self-harm)."""

    def __init__(
        self,
        *,
        categories: tuple[str, ...],
        redirect_resource: RedirectResource | None = None,
    ) -> None:
        super().__init__(f"custom scenario description blocked: {categories}")
        self.categories = categories
        self.redirect_resource = redirect_resource


class CustomScenarioService:
    """Validates, moderates, and registers user-created scenarios."""

    def __init__(self, *, moderation: ModerationService) -> None:
        self._moderation = moderation
        # (user_id, Shanghai date) -> count. In-memory v1 — a restart
        # resets quotas, acceptable for a 10/day abuse guard.
        self._quota: dict[tuple[str, date], int] = {}

    async def create_custom_scenario(
        self,
        *,
        description: str,
        user_id: str,
        is_minor: bool,
        trace_id: str,
        today: date | None = None,
    ) -> ScenarioRecord:
        """Moderate `description`, build a scenario, register it, and
        return it. Raises `CustomScenarioRateLimitedError` (quota) or
        `CustomScenarioBlockedError` (moderation). `today` is injectable
        so tests pin the quota day."""
        day = today or datetime.now(_SHANGHAI_TZ).date()
        self._check_quota(user_id=user_id, day=day)

        decision = await self._moderate(
            description, user_id=user_id, is_minor=is_minor, trace_id=trace_id
        )
        if decision is not None and decision.verdict in ("block", "redirect"):
            logger.info(
                "custom_scenario_blocked",
                user_id=user_id,
                trace_id=trace_id,
                verdict=decision.verdict,
                categories=list(decision.categories),
            )
            raise CustomScenarioBlockedError(
                categories=tuple(decision.categories),
                redirect_resource=decision.redirect_resource,
            )

        record = _build_placeholder(description)
        register_custom_scenario(record)
        self._quota[(user_id, day)] = self._quota.get((user_id, day), 0) + 1
        logger.info(
            "custom_scenario_created",
            user_id=user_id,
            trace_id=trace_id,
            scenario_id=record.id,
        )
        return record

    def _check_quota(self, *, user_id: str, day: date) -> None:
        if self._quota.get((user_id, day), 0) >= _DAILY_QUOTA:
            raise CustomScenarioRateLimitedError(limit=_DAILY_QUOTA)

    async def _moderate(
        self,
        description: str,
        *,
        user_id: str,
        is_minor: bool,
        trace_id: str,
    ) -> ModerationCheckResponse | None:
        """Check the description against the red lines. Returns `None`
        on a backend outage — fail-open mirrors the turn-input path
        (A-37); the generated scenario still gets output-moderated in
        R3-5 before it can be practised against."""
        try:
            return await self._moderation.check(
                ModerationCheckRequest(content=description, context="scenario_custom"),
                user_id=user_id,
                is_minor=is_minor,
                trace_id=trace_id,
            )
        except ModerationBackendError as exc:
            logger.warning(
                "custom_scenario_moderation_unavailable",
                user_id=user_id,
                trace_id=trace_id,
                error=str(exc),
            )
            return None


def _build_placeholder(description: str) -> ScenarioRecord:
    """R3-4 placeholder generator — a templated scenario carrying the
    raw description as its background. R3-5 replaces this with LLM
    generation that derives a real title, persona, and opening line."""
    return ScenarioRecord(
        id=f"sc_custom_{uuid.uuid4().hex[:12]}",
        title=_derive_title(description),
        category="life",  # custom scenarios are uncategorised — default
        difficulty=3,  # mid — R3-5's LLM will assess real difficulty
        tags=(),
        background=description,
        real_user_certified=False,
        persona_title="对手",
        opening_line="我们开始吧，你先说。",
    )


def _derive_title(description: str) -> str:
    """First line of the description, capped — a stand-in title until
    R3-5's LLM produces a real one."""
    first_line = description.strip().splitlines()[0].strip()
    return first_line[:_TITLE_MAX_CHARS]


__all__ = [
    "CustomScenarioBlockedError",
    "CustomScenarioRateLimitedError",
    "CustomScenarioService",
]
