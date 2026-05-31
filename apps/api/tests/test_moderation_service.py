"""Unit tests for `ModerationService` + the Noop backend.

The service is tested with a recording in-memory sink so we can assert
that every call produces a complete audit row, even when the route
layer isn't involved. PR ②/③ tests will swap the backend in this same
fixture style.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field

import pytest
from app.schemas.moderation import (
    ModerationCheckRequest,
    RedirectResource,
)
from app.services.moderation import (
    Decision,
    ModerationService,
    NoopBackend,
)
from app.services.moderation.backend import ModerationBackendError


@dataclass
class _RecordingSink:
    """Captures every recorded event so tests can assert on them."""

    events: list[dict[str, object]] = field(default_factory=list)

    async def record(
        self,
        *,
        request: ModerationCheckRequest,
        user_id: str,
        decision: Decision,
        backend_name: str,
        trace_id: str,
    ) -> None:
        self.events.append(
            {
                "user_id": user_id,
                "session_id": request.session_id,
                "context": request.context,
                "content_length": len(request.content),
                "verdict": decision.verdict,
                "categories": list(decision.categories),
                "score": decision.score,
                "backend": backend_name,
                "trace_id": trace_id,
            }
        )


@dataclass
class _StaticBackend:
    """Backend that always returns the given Decision."""

    decision: Decision
    name: str = "static"

    async def evaluate(
        self,
        content: str,
        context: str,
    ) -> Decision:
        _ = (content, context)
        return self.decision


@dataclass
class _FailingBackend:
    """Backend that always raises `ModerationBackendError`."""

    name: str = "failing"

    async def evaluate(
        self,
        content: str,
        context: str,
    ) -> Decision:
        _ = (content, context)
        raise ModerationBackendError("simulated upstream 502", backend=self.name)


async def test_noop_backend_allows_everything() -> None:
    backend = NoopBackend()
    sink = _RecordingSink()
    service = ModerationService(backend=backend, event_sink=sink)

    response = await service.check(
        ModerationCheckRequest(
            content="今天天气真好，去吃个面",
            context="user_input",
        ),
        user_id="u_test",
        trace_id="trace_001",
    )

    assert response.verdict == "allow"
    assert response.categories == []
    assert response.score == 0.0
    assert response.redirect_resource is None
    assert response.trace_id == "trace_001"


async def test_service_records_audit_event_for_every_call() -> None:
    sink = _RecordingSink()
    service = ModerationService(backend=NoopBackend(), event_sink=sink)

    await service.check(
        ModerationCheckRequest(
            content="hello",
            context="user_input",
            session_id="ses_99",
        ),
        user_id="u_42",
        trace_id="trace_xyz",
    )

    # Audit is fire-and-forget — drain the background write before asserting.
    await service.aclose()
    assert len(sink.events) == 1
    event = sink.events[0]
    assert event["user_id"] == "u_42"
    assert event["session_id"] == "ses_99"
    assert event["context"] == "user_input"
    assert event["content_length"] == len("hello")
    assert event["verdict"] == "allow"
    assert event["backend"] == "noop"
    assert event["trace_id"] == "trace_xyz"


async def test_service_passes_redirect_resource_through() -> None:
    """Self-harm content should be redirected to a help resource."""
    resource = RedirectResource(
        title="心理援助 24h 热线",
        url="tel:010-82951332",
    )
    decision = Decision(
        verdict="redirect",
        score=0.97,
        categories=("self_harm",),
        redirect_resource=resource,
    )
    sink = _RecordingSink()
    service = ModerationService(
        backend=_StaticBackend(decision=decision),
        event_sink=sink,
    )

    response = await service.check(
        ModerationCheckRequest(
            content="想从楼上跳下去",
            context="user_input",
        ),
        user_id="u_minor",
        trace_id="trace_self_harm",
    )

    assert response.verdict == "redirect"
    assert response.categories == ["self_harm"]
    assert response.redirect_resource == resource
    await service.aclose()
    assert sink.events[0]["verdict"] == "redirect"
    assert sink.events[0]["categories"] == ["self_harm"]


async def test_service_propagates_backend_errors() -> None:
    """`ModerationBackendError` bubbles out so the route layer maps it to 502."""
    service = ModerationService(
        backend=_FailingBackend(),
        event_sink=_RecordingSink(),
    )

    with pytest.raises(ModerationBackendError):
        await service.check(
            ModerationCheckRequest(
                content="hello",
                context="user_input",
            ),
            user_id="u_test",
            trace_id="trace_fail",
        )


async def test_audit_failure_does_not_block_response() -> None:
    """If the sink crashes, the user still gets a verdict."""

    class _BrokenSink:
        async def record(self, **_: object) -> None:
            raise RuntimeError("db unreachable")

    service = ModerationService(backend=NoopBackend(), event_sink=_BrokenSink())

    response = await service.check(
        ModerationCheckRequest(
            content="hello",
            context="user_input",
        ),
        user_id="u_test",
        trace_id="trace_db_down",
    )

    assert response.verdict == "allow"
    assert response.trace_id == "trace_db_down"


async def test_check_does_not_block_on_slow_audit_sink() -> None:
    """The verdict must return without waiting on the audit write — a
    hung sink (e.g. an unreachable DB) cannot add latency to the response.
    This is the regression guard for the hot-path audit `await`."""

    @dataclass
    class _HangingSink:
        release: asyncio.Event
        recorded: bool = False

        async def record(self, **_: object) -> None:
            await self.release.wait()  # not released within the assertion window
            self.recorded = True

    sink = _HangingSink(release=asyncio.Event())
    service = ModerationService(backend=NoopBackend(), event_sink=sink)

    response = await asyncio.wait_for(
        service.check(
            ModerationCheckRequest(content="hello", context="user_input"),
            user_id="u_test",
            trace_id="trace_slow_sink",
        ),
        timeout=1.0,
    )

    # Returned immediately; the audit is still in flight.
    assert response.verdict == "allow"
    assert sink.recorded is False

    # Release the background write and drain it cleanly.
    sink.release.set()
    await service.aclose()
    assert sink.recorded is True


async def test_aclose_skips_audit_tasks_from_a_foreign_event_loop() -> None:
    """Regression (PR #199): the service is a process-wide singleton, so
    across a test session `_audit_tasks` can hold tasks created on other,
    now-closed event loops (each `TestClient` context opens its own). The
    shutdown lifespan calls `aclose()` on *every* such teardown — if it
    passed a foreign-loop task to `asyncio.gather` it would raise "got
    Future attached to a different loop", erroring every teardown and
    hanging CI. `aclose()` must skip foreign-loop tasks and drop them."""
    service = ModerationService(backend=NoopBackend(), event_sink=_RecordingSink())

    foreign_loop = asyncio.new_event_loop()
    # A pending Future bound to the foreign loop stands in for an in-flight
    # audit task from another context — `aclose` only inspects `.get_loop()`
    # and `.done()`, and a bare Future avoids a "coroutine never awaited"
    # warning at cleanup.
    foreign_future: asyncio.Future[None] = foreign_loop.create_future()
    service._audit_tasks.add(foreign_future)  # type: ignore[arg-type]

    try:
        # Must not raise despite the cross-loop future...
        await service.aclose()
        # ...and must have dropped its reference so a later drain is cheap.
        assert service._audit_tasks == set()
    finally:
        foreign_future.cancel()
        foreign_loop.close()


def test_decision_rejects_out_of_range_score() -> None:
    with pytest.raises(ValueError, match=r"score must be in \[0, 1\]"):
        Decision(verdict="allow", score=1.5)


def test_decision_redirect_requires_resource() -> None:
    with pytest.raises(ValueError, match="redirect_resource"):
        Decision(verdict="redirect", score=0.9)


def test_decision_allow_must_not_carry_categories() -> None:
    with pytest.raises(ValueError, match="must not list categories"):
        Decision(verdict="allow", score=0.0, categories=("self_harm",))
