"""CascadingBackend failover tests.

The cascade is the whole point of pairing Aliyun with the local dict:
when Aliyun is slow / down, red-line decisions still get made.

We test the cascade independent of either real backend — all fixtures
here are tiny stub backends so failure-mode behaviour is unambiguous.
"""

from __future__ import annotations

import asyncio

import pytest
from app.services.moderation.backend import ModerationBackendError
from app.services.moderation.cascading import CascadingBackend
from app.services.moderation.local_dict import SELF_HARM_RESOURCE
from app.services.moderation.types import Decision


class _StaticBackend:
    """Returns the same Decision every time."""

    def __init__(self, name: str, decision: Decision) -> None:
        self.name = name
        self._decision = decision

    async def evaluate(self, content: str, context: str) -> Decision:
        _ = (content, context)
        return self._decision


class _SlowBackend:
    """Sleeps until cancelled — used to exercise the cascade timeout."""

    name = "slow"

    async def evaluate(self, content: str, context: str) -> Decision:
        _ = (content, context)
        await asyncio.sleep(10)
        raise AssertionError("slow backend should have been cancelled")


class _BoomBackend:
    """Always raises `ModerationBackendError`."""

    def __init__(self, name: str = "boom") -> None:
        self.name = name

    async def evaluate(self, content: str, context: str) -> Decision:
        _ = (content, context)
        raise ModerationBackendError("simulated upstream 502", backend=self.name)


class _CountingAllowBackend:
    """Allows everything, counting how often it was consulted — used to
    prove the floor does NOT re-check the backup on a cloud non-allow."""

    def __init__(self, name: str = "counting") -> None:
        self.name = name
        self.calls = 0

    async def evaluate(self, content: str, context: str) -> Decision:
        _ = (content, context)
        self.calls += 1
        return Decision(verdict="allow", score=0.0)


CLOUD_DECISION = Decision(verdict="block", score=0.99, categories=("violence",))
LOCAL_DECISION = Decision(verdict="allow", score=0.0)
CLOUD_ALLOW = Decision(verdict="allow", score=0.0)
# What the local dict returns for a self-harm line the cloud chat scene
# fails to label — the red-line the floor exists to catch.
LOCAL_SELF_HARM = Decision(
    verdict="redirect",
    score=0.99,
    categories=("self_harm",),
    redirect_resource=SELF_HARM_RESOURCE,
)


async def test_primary_decides_when_it_responds_in_time() -> None:
    cascade = CascadingBackend(
        primary=_StaticBackend("primary", CLOUD_DECISION),
        backup=_StaticBackend("backup", LOCAL_DECISION),
    )

    decision = await cascade.evaluate("打他", "user_input")

    assert decision == CLOUD_DECISION


async def test_falls_back_to_backup_on_primary_timeout() -> None:
    cascade = CascadingBackend(
        primary=_SlowBackend(),
        backup=_StaticBackend("backup", LOCAL_DECISION),
        timeout_s=0.05,
    )

    decision = await cascade.evaluate("hi", "user_input")

    assert decision == LOCAL_DECISION


async def test_falls_back_to_backup_on_primary_error() -> None:
    cascade = CascadingBackend(
        primary=_BoomBackend(),
        backup=_StaticBackend("backup", LOCAL_DECISION),
    )

    decision = await cascade.evaluate("hi", "user_input")

    assert decision == LOCAL_DECISION


async def test_backup_errors_bubble_up_when_primary_also_failed() -> None:
    cascade = CascadingBackend(
        primary=_BoomBackend(name="primary"),
        backup=_BoomBackend(name="backup"),
    )

    with pytest.raises(ModerationBackendError):
        await cascade.evaluate("hi", "user_input")


async def test_backup_errors_propagate_when_primary_timed_out() -> None:
    cascade = CascadingBackend(
        primary=_SlowBackend(),
        backup=_BoomBackend(name="backup"),
        timeout_s=0.05,
    )

    with pytest.raises(ModerationBackendError):
        await cascade.evaluate("hi", "user_input")


def test_name_advertises_both_arms() -> None:
    cascade = CascadingBackend(
        primary=_StaticBackend("aliyun", CLOUD_DECISION),
        backup=_StaticBackend("local_dict", LOCAL_DECISION),
    )

    assert cascade.name == "cascading[aliyun>local_dict]"


def test_rejects_non_positive_timeout() -> None:
    with pytest.raises(ValueError, match="must be positive"):
        CascadingBackend(
            primary=_StaticBackend("p", CLOUD_DECISION),
            backup=_StaticBackend("b", LOCAL_DECISION),
            timeout_s=0,
        )


# --- red-line floor: a cloud `allow` is re-checked against the dict ---


async def test_cloud_allow_is_overridden_by_local_red_line_floor() -> None:
    """The core safety property: the cloud chat scene does not label
    self-harm, so a cloud `allow` must NOT let self-harm through — the
    local dict's redirect wins."""
    cascade = CascadingBackend(
        primary=_StaticBackend("aliyun", CLOUD_ALLOW),
        backup=_StaticBackend("local_dict", LOCAL_SELF_HARM),
    )

    decision = await cascade.evaluate("我不想活了想跳楼", "user_input")

    assert decision == LOCAL_SELF_HARM
    assert decision.verdict == "redirect"


async def test_cloud_allow_stands_when_local_also_allows() -> None:
    cascade = CascadingBackend(
        primary=_StaticBackend("aliyun", CLOUD_ALLOW),
        backup=_StaticBackend("local_dict", LOCAL_DECISION),
    )

    decision = await cascade.evaluate("今天天气不错", "user_input")

    assert decision == CLOUD_ALLOW
    assert decision.verdict == "allow"


async def test_cloud_block_is_trusted_without_consulting_backup() -> None:
    """A cloud non-allow caught something — trust it directly and do NOT
    burn a backup call (the floor only guards cloud allows)."""
    backup = _CountingAllowBackend("local_dict")
    cascade = CascadingBackend(
        primary=_StaticBackend("aliyun", CLOUD_DECISION),
        backup=backup,
    )

    decision = await cascade.evaluate("你这个废物", "user_input")

    assert decision == CLOUD_DECISION
    assert backup.calls == 0


async def test_floor_backup_error_keeps_cloud_allow() -> None:
    """If the floor re-check itself errors, fail open on the floor (keep
    the cloud allow) rather than 502 the whole request."""
    cascade = CascadingBackend(
        primary=_StaticBackend("aliyun", CLOUD_ALLOW),
        backup=_BoomBackend(name="local_dict"),
    )

    decision = await cascade.evaluate("ambiguous", "user_input")

    assert decision == CLOUD_ALLOW
