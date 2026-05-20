"""`CustomScenarioService` behaviour — POST /v1/scenarios/custom.

Covers the moderation gates (input + generated output), the 10/day
quota, LLM generation + parse fallback, and registration of the
generated scenario so `POST /v1/sessions` can practise its id.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import date

import pytest
from app.llm import Message, TokenUsage
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

# A generation response that follows the four-line labelled format.
_GOOD_GEN = (
    "TITLE: 向房东追讨押金\n"
    "PERSONA: 精明的房东\n"
    "OPENING: 押金的事，我得先翻翻合同再说。\n"
    "BACKGROUND: 租约到期，合同押金条款含糊，你要据理力争拿回押金。"
)

# A response that ignores the format — drives the placeholder fallback.
_BAD_GEN = "这是一段没有按格式输出的普通回复文字。"


class _ScriptedGenLLM:
    """Minimal `LLMProvider` stub — yields one fixed response."""

    name = "scripted-gen"

    def __init__(self, response: str) -> None:
        self._response = response

    async def stream_chat(
        self,
        messages: list[Message],
        *,
        temperature: float = 0.7,
        timeout: float = 8.0,
        usage_sink: list[TokenUsage] | None = None,
    ) -> AsyncIterator[str]:
        _ = (messages, temperature, timeout, usage_sink)
        yield self._response


def _allow_moderation() -> ModerationService:
    """NoopBackend → every check passes moderation."""
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


def _allow_then_block_moderation() -> ModerationService:
    """First check (the description) passes; the second (the generated
    script) is blocked — exercises the output-moderation gate."""

    class _Backend:
        name = "test_allow_then_block"

        def __init__(self) -> None:
            self._calls = 0

        async def evaluate(self, content: str, context: str) -> Decision:
            _ = (content, context)
            self._calls += 1
            if self._calls == 1:
                return Decision(verdict="allow", score=0.1)
            return Decision(verdict="block", score=0.99, categories=("violence",))

    return ModerationService(backend=_Backend(), event_sink=LogOnlyEventSink())


def _service(
    *,
    moderation: ModerationService | None = None,
    gen: str = _GOOD_GEN,
) -> CustomScenarioService:
    return CustomScenarioService(
        moderation=moderation or _allow_moderation(),
        llm=_ScriptedGenLLM(gen),
    )


async def test_create_generates_and_registers_scenario() -> None:
    """The LLM-generated title / persona / opening land on the record,
    and it is resolvable by `get_record_by_id` — so a follow-up
    `POST /v1/sessions` can practise its id immediately."""
    svc = _service()
    record = await svc.create_custom_scenario(
        description=_DESC, user_id="u_1", is_minor=False, trace_id="t1"
    )
    assert record.id.startswith("sc_custom_")
    assert record.title == "向房东追讨押金"
    assert record.persona_title == "精明的房东"
    assert record.opening_line == "押金的事，我得先翻翻合同再说。"
    assert get_record_by_id(record.id).id == record.id


async def test_unparseable_generation_falls_back_to_placeholder() -> None:
    """If the model ignores the labelled format the user still gets a
    practisable scenario — the templated placeholder."""
    svc = _service(gen=_BAD_GEN)
    record = await svc.create_custom_scenario(
        description=_DESC, user_id="u_1", is_minor=False, trace_id="t1"
    )
    assert record.persona_title == "对手"  # placeholder persona
    assert record.background == _DESC  # placeholder uses the raw description


async def test_blocked_description_raises_blocked_error() -> None:
    svc = _service(moderation=_blocking_moderation(verdict="block"))
    with pytest.raises(CustomScenarioBlockedError) as exc_info:
        await svc.create_custom_scenario(
            description=_DESC, user_id="u_1", is_minor=False, trace_id="t1"
        )
    assert exc_info.value.categories


async def test_redirect_description_carries_crisis_resource() -> None:
    """A self-harm description (verdict=redirect) is rejected, and the
    crisis-line resource rides on the error like the H-1 path."""
    svc = _service(moderation=_blocking_moderation(verdict="redirect"))
    with pytest.raises(CustomScenarioBlockedError) as exc_info:
        await svc.create_custom_scenario(
            description=_DESC, user_id="u_1", is_minor=False, trace_id="t1"
        )
    assert exc_info.value.redirect_resource is not None
    assert exc_info.value.redirect_resource.title


async def test_output_moderation_blocks_unsafe_generated_scenario() -> None:
    """A benign description can still yield a red-line generated script
    (the opponent persona is adversarial). The generated scenario is
    moderated too — a block on it rejects the create."""
    svc = _service(moderation=_allow_then_block_moderation())
    with pytest.raises(CustomScenarioBlockedError) as exc_info:
        await svc.create_custom_scenario(
            description=_DESC, user_id="u_1", is_minor=False, trace_id="t1"
        )
    assert exc_info.value.categories
    # Output-stage block — no crisis resource (LLM drift, not user distress).
    assert exc_info.value.redirect_resource is None


async def test_daily_quota_blocks_the_eleventh_create() -> None:
    svc = _service()
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
    svc = _service()
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
    svc = _service(moderation=_blocking_moderation(verdict="block"))
    day = date(2026, 5, 20)
    for _ in range(15):
        with pytest.raises(CustomScenarioBlockedError):
            await svc.create_custom_scenario(
                description=_DESC, user_id="u_1", is_minor=False, trace_id="t", today=day
            )
