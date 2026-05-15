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
from app.services.auth import mint_token
from app.services.moderation import (
    LogOnlyEventSink,
    ModerationService,
    NoopBackend,
    get_moderation_service,
)
from httpx import ASGITransport, AsyncClient

_DEFAULT_TEST_USER_ID = "u_default_test"


def _default_auth_headers() -> dict[str, str]:
    token = mint_token(
        user_id=_DEFAULT_TEST_USER_ID,
        persona_type="intern",
        is_minor=False,
    )
    return {"Authorization": f"Bearer {token}"}


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
    async with AsyncClient(
        transport=transport,
        base_url="http://test",
        headers=_default_auth_headers(),
    ) as ac:
        yield ac


@pytest.fixture
async def anon_client(
    service_override: ModerationService,
) -> AsyncIterator[AsyncClient]:
    """Client with no default Authorization header — for 401-branch tests."""
    _ = service_override
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


async def test_moderation_check_returns_allow_for_benign_text(client: AsyncClient) -> None:
    resp = await client.post(
        "/v1/moderation/check",
        json={
            "content": "今天天气真好",
            "context": "user_input",
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
    """Test sink that snapshots the (`request`, `user_id`) pair it receives.

    Tests assert on `.recorded[-1][1]` to verify the route layer hands
    the sink the JWT-derived user id — not whatever a caller might have
    stuffed into the request body. We don't care about the decision /
    backend_name fields here.
    """

    def __init__(self) -> None:
        from app.schemas.moderation import ModerationCheckRequest

        self.recorded: list[tuple[ModerationCheckRequest, str]] = []

    async def record(
        self,
        *,
        request: object,
        user_id: str,
        decision: object,
        backend_name: str,
        trace_id: str,
    ) -> None:
        from app.schemas.moderation import ModerationCheckRequest

        assert isinstance(request, ModerationCheckRequest)
        self.recorded.append((request, user_id))


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
    async with AsyncClient(
        transport=transport,
        base_url="http://test",
        headers=_default_auth_headers(),
    ) as ac:
        yield ac, capturing_sink_override


@pytest.fixture
async def anon_capturing_client(
    capturing_sink_override: _CapturingEventSink,
) -> AsyncIterator[tuple[AsyncClient, _CapturingEventSink]]:
    """Capturing client without the default Authorization header — for
    the 401-branch tests that assert the request never even reaches the
    sink because the dependency rejects upstream."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac, capturing_sink_override


async def test_audit_row_uses_jwt_user_id_when_bearer_token_present(
    capturing_client: tuple[AsyncClient, _CapturingEventSink],
) -> None:
    """JWT-derived id is authoritative — the route hands it to the sink
    as a separate argument so a caller can't frame another user."""
    from app.services.auth import mint_token

    client, sink = capturing_client
    token = mint_token(user_id="u_real_caller", persona_type="intern", is_minor=False)

    resp = await client.post(
        "/v1/moderation/check",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "content": "hi",
            "context": "user_input",
        },
    )

    assert resp.status_code == 200
    assert len(sink.recorded) == 1
    # The audit row carries the JWT-derived id, NOT the request body.
    _, recorded_user_id = sink.recorded[0]
    assert recorded_user_id == "u_real_caller"


async def test_extra_user_id_in_body_is_ignored_not_attributed(
    capturing_client: tuple[AsyncClient, _CapturingEventSink],
) -> None:
    """The framing defense: even if a caller still sends `user_id` in
    the body (old client, attack attempt, …), Pydantic's `extra='ignore'`
    drops it and the audit row stays on the JWT-derived id.

    This pins the contract: dropping `user_id` from the schema MUST NOT
    leave any path where a self-reported id leaks into the audit log.
    """
    from app.services.auth import mint_token

    client, sink = capturing_client
    token = mint_token(user_id="u_real_caller", persona_type="intern", is_minor=False)

    resp = await client.post(
        "/v1/moderation/check",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "content": "hi",
            "context": "user_input",
            "user_id": "u_spoofed_victim",  # explicitly stuffed — must be ignored
        },
    )

    assert resp.status_code == 200
    request_obj, recorded_user_id = sink.recorded[0]
    # Schema doesn't expose the field at all, so it never lands on the
    # request model — and the sink got the JWT id, not the spoofed one.
    assert not hasattr(request_obj, "user_id")
    assert recorded_user_id == "u_real_caller"


async def test_request_without_bearer_token_returns_401(
    anon_capturing_client: tuple[AsyncClient, _CapturingEventSink],
) -> None:
    """Hard auth: no token → 401 before the moderation service runs.
    The capturing sink must be empty — the request never reached the
    audit path. We deliberately stuff `user_id` in the body to prove
    that an unauthenticated caller can't seed audit rows by lying about
    identity either."""
    client, sink = anon_capturing_client

    resp = await client.post(
        "/v1/moderation/check",
        json={
            "content": "hi",
            "context": "user_input",
            "user_id": "u_spoofed_anonymous",
        },
    )

    assert resp.status_code == 401
    assert resp.json()["code"] == "UNAUTHORIZED"
    # No audit row should be written for a request that was rejected at
    # the auth boundary — otherwise an unauthenticated attacker could
    # pollute the moderation event stream.
    assert sink.recorded == []


async def test_request_with_invalid_bearer_token_returns_401(
    anon_capturing_client: tuple[AsyncClient, _CapturingEventSink],
) -> None:
    """Tampered / expired / garbage tokens get the same 401 treatment as
    no token at all."""
    client, sink = anon_capturing_client

    resp = await client.post(
        "/v1/moderation/check",
        headers={"Authorization": "Bearer not-a-real-jwt"},
        json={
            "content": "hi",
            "context": "user_input",
        },
    )

    assert resp.status_code == 401
    assert resp.json()["code"] == "UNAUTHORIZED"
    assert sink.recorded == []
