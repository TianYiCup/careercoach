"""`CustomScenarioService` behaviour — POST /v1/scenarios/custom (R3-4).

Covers the moderation gate, the 10/day quota, and registration of the
generated scenario so `POST /v1/sessions` can practise its id.
"""

from __future__ import annotations

from datetime import date

import pytest
from app.schemas.moderation import RedirectResource
from app.services.moderation import LogOnlyEventSink, ModerationService, NoopBackend
from app.services.moderation.types import Decision
from app.services.scenarios.custom import (
    CustomScenarioBlockedError,
    CustomScenarioRateLimitedError,
    CustomScenarioService,
)
from app.services.scenarios.seed_data import get_record_by_id

# A description comfortably over the 30-char US-A1 minimum.
_DESC = "我想练习向房东要求退还押金，但租房合同里关于押金的条款写得很模糊，需要一些谈判技巧。"


def _allow_moderation() -> ModerationService:
    """NoopBackend → every description passes moderation."""
    return ModerationService(backend=NoopBackend(), event_sink=LogOnlyEventSink())


def _blocking_moderation(*, verdict: str = "block") -> ModerationService:
    """Backend that returns `block` (or `redirect`) for every input."""

    class _Backend:
        name = "test_block"

        async def evaluate(self, content: str, context: str) -> Decision:
            _ = (content, context)
            resource = (
                RedirectResource(title="心理援助 24h 热线", url="tel:010-82951332")
                if verdict == "redirect"
                else None
            )
            return Decision(
                verdict=verdict,  # type: ignore[arg-type]
                score=0.99,
                categories=("self_harm",),
                redirect_resource=resource,
            )

    return ModerationService(backend=_Backend(), event_sink=LogOnlyEventSink())


async def test_create_returns_record_and_registers_it() -> None:
    """A created scenario is resolvable by `get_record_by_id` — so a
    follow-up `POST /v1/sessions` can practise its id immediately."""
    svc = CustomScenarioService(moderation=_allow_moderation())
    record = await svc.create_custom_scenario(
        description=_DESC, user_id="u_1", is_minor=False, trace_id="t1"
    )
    assert record.id.startswith("sc_custom_")
    assert record.background == _DESC
    assert get_record_by_id(record.id).id == record.id


async def test_blocked_description_raises_blocked_error() -> None:
    svc = CustomScenarioService(moderation=_blocking_moderation(verdict="block"))
    with pytest.raises(CustomScenarioBlockedError) as exc_info:
        await svc.create_custom_scenario(
            description=_DESC, user_id="u_1", is_minor=False, trace_id="t1"
        )
    assert exc_info.value.categories


async def test_redirect_description_carries_crisis_resource() -> None:
    """A self-harm description (verdict=redirect) is rejected, and the
    crisis-line resource rides on the error like the H-1 path."""
    svc = CustomScenarioService(moderation=_blocking_moderation(verdict="redirect"))
    with pytest.raises(CustomScenarioBlockedError) as exc_info:
        await svc.create_custom_scenario(
            description=_DESC, user_id="u_1", is_minor=False, trace_id="t1"
        )
    assert exc_info.value.redirect_resource is not None
    assert exc_info.value.redirect_resource.title


async def test_daily_quota_blocks_the_eleventh_create() -> None:
    svc = CustomScenarioService(moderation=_allow_moderation())
    day = date(2026, 5, 20)
    for _ in range(10):
        await svc.create_custom_scenario(
            description=_DESC, user_id="u_1", is_minor=False, trace_id="t", today=day
        )
    with pytest.raises(CustomScenarioRateLimitedError) as exc_info:
        await svc.create_custom_scenario(
            description=_DESC, user_id="u_1", is_minor=False, trace_id="t", today=day
        )
    assert exc_info.value.limit == 10


async def test_quota_isolates_users_and_days() -> None:
    svc = CustomScenarioService(moderation=_allow_moderation())
    for _ in range(10):
        await svc.create_custom_scenario(
            description=_DESC,
            user_id="u_1",
            is_minor=False,
            trace_id="t",
            today=date(2026, 5, 20),
        )
    # A different user has their own fresh quota.
    await svc.create_custom_scenario(
        description=_DESC,
        user_id="u_2",
        is_minor=False,
        trace_id="t",
        today=date(2026, 5, 20),
    )
    # The same user the next day has a fresh quota too.
    await svc.create_custom_scenario(
        description=_DESC,
        user_id="u_1",
        is_minor=False,
        trace_id="t",
        today=date(2026, 5, 21),
    )


async def test_blocked_attempts_do_not_consume_quota() -> None:
    """A blocked description never reaches the generator, so it must
    not burn a quota slot — 15 blocked attempts all surface as
    BlockedError, never RateLimitedError."""
    svc = CustomScenarioService(moderation=_blocking_moderation(verdict="block"))
    day = date(2026, 5, 20)
    for _ in range(15):
        with pytest.raises(CustomScenarioBlockedError):
            await svc.create_custom_scenario(
                description=_DESC, user_id="u_1", is_minor=False, trace_id="t", today=day
            )
