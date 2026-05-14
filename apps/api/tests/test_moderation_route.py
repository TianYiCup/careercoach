"""Route-level smoke for `POST /v1/moderation/check`.

PR ① wires the route to `ModerationService` with the Noop backend, so
every request should return `verdict=allow` and echo the supplied
`x-request-id`. We override the service dependency with a Log-only
event sink to keep tests out of the DB.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterator

import pytest
from app.main import app
from app.services.moderation import (
    LogOnlyEventSink,
    ModerationService,
    NoopBackend,
    get_moderation_service,
)
from httpx import ASGITransport, AsyncClient


@pytest.fixture
def service_override() -> Iterator[ModerationService]:
    """Swap the DB-backed default service for a log-only one in tests."""
    service = ModerationService(backend=NoopBackend(), event_sink=LogOnlyEventSink())
    app.dependency_overrides[get_moderation_service] = lambda: service
    try:
        yield service
    finally:
        app.dependency_overrides.pop(get_moderation_service, None)


@pytest.fixture
async def client(
    service_override: ModerationService,
) -> AsyncIterator[AsyncClient]:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


async def test_moderation_check_returns_allow_for_benign_text(client: AsyncClient) -> None:
    resp = await client.post(
        "/v1/moderation/check",
        json={
            "content": "今天天气真好",
            "context": "user_input",
            "user_id": "u_test",
        },
    )

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["verdict"] == "allow"
    assert body["categories"] == []
    assert body["score"] == 0.0
    assert body["redirect_resource"] is None
    assert body["trace_id"]


async def test_moderation_check_echoes_trace_id_header(client: AsyncClient) -> None:
    resp = await client.post(
        "/v1/moderation/check",
        headers={"x-request-id": "trace-from-header-001"},
        json={
            "content": "hi",
            "context": "user_input",
            "user_id": "u_test",
        },
    )

    assert resp.status_code == 200
    assert resp.json()["trace_id"] == "trace-from-header-001"


async def test_moderation_check_rejects_empty_content(client: AsyncClient) -> None:
    resp = await client.post(
        "/v1/moderation/check",
        json={
            "content": "",
            "context": "user_input",
            "user_id": "u_test",
        },
    )

    assert resp.status_code == 422
    body = resp.json()
    assert body["code"] == "VALIDATION_ERROR"
    assert body["trace_id"]


async def test_moderation_check_rejects_unknown_context(client: AsyncClient) -> None:
    resp = await client.post(
        "/v1/moderation/check",
        json={
            "content": "hi",
            "context": "not_a_context",
            "user_id": "u_test",
        },
    )

    assert resp.status_code == 422


async def test_moderation_response_is_locked_in_openapi() -> None:
    """The 200 schema must be `ModerationCheckResponse` so B's codegen stays stable."""
    spec = app.openapi()
    op_spec = spec["paths"]["/v1/moderation/check"]["post"]
    schema_ref = op_spec["responses"]["200"]["content"]["application/json"]["schema"]["$ref"]

    assert schema_ref.endswith("/ModerationCheckResponse")


# --- Auth override: JWT user_id is authoritative for the audit row ---


class _CapturingEventSink:
    """Test sink that snapshots the `ModerationCheckRequest` it receives.

    Tests assert on `.recorded[-1].user_id` to verify the route layer
    overrode the payload's self-reported user_id with the JWT-derived
    one. We don't care about the decision / backend_name fields here.
    """

    def __init__(self) -> None:
        from app.schemas.moderation import ModerationCheckRequest

        self.recorded: list[ModerationCheckRequest] = []

    async def record(
        self,
        *,
        request: object,
        decision: object,
        backend_name: str,
        trace_id: str,
    ) -> None:
        from app.schemas.moderation import ModerationCheckRequest

        assert isinstance(request, ModerationCheckRequest)
        self.recorded.append(request)


@pytest.fixture
def capturing_sink_override() -> Iterator[_CapturingEventSink]:
    """Replace the service with one that captures every request the
    route hands down. Used to assert audit-row identity below."""
    sink = _CapturingEventSink()
    service = ModerationService(backend=NoopBackend(), event_sink=sink)  # type: ignore[arg-type]
    app.dependency_overrides[get_moderation_service] = lambda: service
    try:
        yield sink
    finally:
        app.dependency_overrides.pop(get_moderation_service, None)


@pytest.fixture
async def capturing_client(
    capturing_sink_override: _CapturingEventSink,
) -> AsyncIterator[tuple[AsyncClient, _CapturingEventSink]]:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac, capturing_sink_override


async def test_audit_row_uses_jwt_user_id_when_bearer_token_present(
    capturing_client: tuple[AsyncClient, _CapturingEventSink],
) -> None:
    """JWT-derived id is authoritative — even if the payload claims
    someone else's id, the audit row gets the real caller."""
    from app.services.auth import mint_token

    client, sink = capturing_client
    token = mint_token(user_id="u_real_caller", persona_type="intern", is_minor=False)

    resp = await client.post(
        "/v1/moderation/check",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "content": "hi",
            "context": "user_input",
            "user_id": "u_spoofed_victim",  # client-claimed id
        },
    )

    assert resp.status_code == 200
    assert len(sink.recorded) == 1
    # The audit row carries the JWT-derived id, NOT the spoofed one
    # the client put in the payload. This is the framing defense.
    assert sink.recorded[0].user_id == "u_real_caller"


async def test_audit_row_uses_anonymous_sentinel_without_bearer_token(
    capturing_client: tuple[AsyncClient, _CapturingEventSink],
) -> None:
    """Soft auth path: no token → anonymous. The route still serves
    (so dev / B's MSW path works) but the audit truth is the
    sentinel, not whatever the client put in the body."""
    client, sink = capturing_client

    resp = await client.post(
        "/v1/moderation/check",
        json={
            "content": "hi",
            "context": "user_input",
            "user_id": "u_spoofed_anonymous",
        },
    )

    assert resp.status_code == 200
    assert len(sink.recorded) == 1
    assert sink.recorded[0].user_id == "anonymous"


async def test_audit_row_uses_anonymous_for_invalid_bearer_token(
    capturing_client: tuple[AsyncClient, _CapturingEventSink],
) -> None:
    """Tampered / expired / garbage tokens get the same anonymous
    treatment as no token at all — same soft-auth fallback rule the
    sessions + sharecards routes use."""
    client, sink = capturing_client

    resp = await client.post(
        "/v1/moderation/check",
        headers={"Authorization": "Bearer not-a-real-jwt"},
        json={
            "content": "hi",
            "context": "user_input",
            "user_id": "u_claim",
        },
    )

    assert resp.status_code == 200
    assert sink.recorded[0].user_id == "anonymous"
