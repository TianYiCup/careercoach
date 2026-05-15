"""Service + route tests for the minor-mode strict tier (PRD §3.0.5 C).

Policy under test:
  * adult       — verdict unchanged across the board
  * minor warn  → block       (the meaningful tighter outcome)
  * minor allow → allow       (don't block benign content)
  * minor redirect → redirect (PRESERVE the crisis-line — blocking a
                                self-harm signal would deny help)
  * minor block → block       (already strictest)

Pinned at two layers:
  * service level (unit): pure logic on a `StaticBackend`
  * route level (integration): `is_minor` flowed from JWT to the verdict
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterator
from dataclasses import dataclass

import pytest
from app.main import app
from app.schemas.moderation import (
    ModerationCheckRequest,
    RedirectResource,
)
from app.services.auth import mint_token
from app.services.moderation import (
    Decision,
    LogOnlyEventSink,
    ModerationService,
    get_moderation_service,
)
from httpx import ASGITransport, AsyncClient


@dataclass
class _StaticBackend:
    """Returns the same decision every time — lets each test pin one
    verdict + score and assert what the strict tier does to it."""

    decision: Decision
    name: str = "static"

    async def evaluate(self, content: str, context: str) -> Decision:
        _ = (content, context)
        return self.decision


# --- Service-level: pure verdict transformation --------------------------------


async def test_minor_warn_is_elevated_to_block() -> None:
    backend = _StaticBackend(Decision(verdict="warn", score=0.6, categories=("harassment",)))
    service = ModerationService(backend=backend, event_sink=LogOnlyEventSink())

    response = await service.check(
        ModerationCheckRequest(content="hi", context="user_input"),
        user_id="u_minor",
        is_minor=True,
        trace_id="trace_minor_warn",
    )

    assert response.verdict == "block"
    # Categories carry through so the audit + client UI can still
    # explain WHY the block happened.
    assert response.categories == ["harassment"]


async def test_minor_redirect_is_preserved_for_crisis_lines() -> None:
    """The non-obvious branch: a self-harm `redirect` must NOT be
    elevated to `block` for minors — they need the help resource."""
    resource = RedirectResource(title="心理援助 24h 热线", url="tel:010-82951332")
    backend = _StaticBackend(
        Decision(
            verdict="redirect",
            score=0.97,
            categories=("self_harm",),
            redirect_resource=resource,
        )
    )
    service = ModerationService(backend=backend, event_sink=LogOnlyEventSink())

    response = await service.check(
        ModerationCheckRequest(content="想从楼上跳下去", context="user_input"),
        user_id="u_minor",
        is_minor=True,
        trace_id="trace_minor_redirect",
    )

    assert response.verdict == "redirect"
    assert response.redirect_resource == resource


async def test_minor_allow_stays_allow() -> None:
    backend = _StaticBackend(Decision(verdict="allow", score=0.0))
    service = ModerationService(backend=backend, event_sink=LogOnlyEventSink())

    response = await service.check(
        ModerationCheckRequest(content="今天天气真好", context="user_input"),
        user_id="u_minor",
        is_minor=True,
        trace_id="trace_minor_allow",
    )

    assert response.verdict == "allow"


async def test_minor_block_stays_block() -> None:
    backend = _StaticBackend(Decision(verdict="block", score=0.95, categories=("violence",)))
    service = ModerationService(backend=backend, event_sink=LogOnlyEventSink())

    response = await service.check(
        ModerationCheckRequest(content="...", context="user_input"),
        user_id="u_minor",
        is_minor=True,
        trace_id="trace_minor_block",
    )

    assert response.verdict == "block"


async def test_adult_warn_stays_warn() -> None:
    """Sanity counterpart: same `warn` decision must NOT be elevated
    for an adult."""
    backend = _StaticBackend(Decision(verdict="warn", score=0.6, categories=("harassment",)))
    service = ModerationService(backend=backend, event_sink=LogOnlyEventSink())

    response = await service.check(
        ModerationCheckRequest(content="hi", context="user_input"),
        user_id="u_adult",
        is_minor=False,
        trace_id="trace_adult_warn",
    )

    assert response.verdict == "warn"


# --- Route-level: is_minor flows from JWT through to the verdict --------------


@pytest.fixture
def warn_backend_override() -> Iterator[None]:
    """Override the global moderation service with one that returns a
    static `warn` decision so route-level tests can assert the JWT's
    `is_minor` actually drives the elevation."""
    service = ModerationService(
        backend=_StaticBackend(Decision(verdict="warn", score=0.6, categories=("harassment",))),
        event_sink=LogOnlyEventSink(),
    )
    app.dependency_overrides[get_moderation_service] = lambda: service
    try:
        yield
    finally:
        app.dependency_overrides.pop(get_moderation_service, None)


@pytest.fixture
async def client(warn_backend_override: None) -> AsyncIterator[AsyncClient]:
    _ = warn_backend_override
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


async def test_minor_jwt_drives_warn_to_block_via_route(client: AsyncClient) -> None:
    """End-to-end: minor JWT → backend returns warn → route surfaces
    block. Proves the JWT flag is actually plumbed through the route
    layer (not just sitting in the service unit tests)."""
    minor_token = mint_token(
        user_id="u_minor", persona_type="in_school", is_minor=True, age_set=True
    )

    resp = await client.post(
        "/v1/moderation/check",
        headers={"Authorization": f"Bearer {minor_token}"},
        json={"content": "hi", "context": "user_input"},
    )

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["verdict"] == "block"


async def test_adult_jwt_keeps_warn_via_route(client: AsyncClient) -> None:
    """Counter-test: same content, adult JWT → warn stays warn."""
    adult_token = mint_token(user_id="u_adult", persona_type="intern", is_minor=False, age_set=True)

    resp = await client.post(
        "/v1/moderation/check",
        headers={"Authorization": f"Bearer {adult_token}"},
        json={"content": "hi", "context": "user_input"},
    )

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["verdict"] == "warn"
