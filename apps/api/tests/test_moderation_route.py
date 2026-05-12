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
