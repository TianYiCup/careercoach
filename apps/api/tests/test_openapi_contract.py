"""Lock the v0.1 endpoint surface (sprint-0-task-split §2.1).

If you intentionally add/remove an endpoint, update REQUIRED_ENDPOINTS and
re-run `uv run python scripts/dump_openapi.py` so apps/api/openapi.yaml stays
in sync. The frontend (B) generates types from that file.
"""

from collections.abc import AsyncIterator

import pytest
from app.main import app
from httpx import ASGITransport, AsyncClient

# (method, path) pairs that B promises to consume from openapi.yaml v0.1.
REQUIRED_ENDPOINTS: set[tuple[str, str]] = {
    ("get", "/health"),
    ("post", "/v1/auth/sms/send"),
    ("post", "/v1/auth/sms/verify"),
    ("post", "/v1/users/me/birth-year"),
    ("get", "/v1/scenarios"),
    ("post", "/v1/sessions"),
    ("post", "/v1/sessions/{session_id}/turns"),
    ("post", "/v1/sessions/{session_id}/end"),
    ("post", "/v1/moderation/check"),
    ("post", "/v1/sharecards/session/{session_id}"),
    ("post", "/v1/sharecards/weekly"),
    ("post", "/v1/sharecards/wrapped/year/{year}"),
}


def _spec_endpoints() -> set[tuple[str, str]]:
    spec = app.openapi()
    found: set[tuple[str, str]] = set()
    for path, methods in spec["paths"].items():
        for method in methods:
            if method.lower() in {"get", "post", "put", "patch", "delete"}:
                found.add((method.lower(), path))
    return found


def test_v01_endpoint_set_matches_contract() -> None:
    found = _spec_endpoints()
    missing = REQUIRED_ENDPOINTS - found
    assert not missing, f"missing v0.1 endpoints: {missing}"


def test_no_drift_outside_v01_surface() -> None:
    # Anything new in v0.x must be added to REQUIRED_ENDPOINTS so B sees it.
    extras = {(m, p) for (m, p) in _spec_endpoints() if p == "/health" or p.startswith("/v1/")}
    drift = extras - REQUIRED_ENDPOINTS
    assert not drift, f"endpoints not declared in REQUIRED_ENDPOINTS: {drift}"


@pytest.fixture
async def client() -> AsyncIterator[AsyncClient]:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


# NOTE: Every v0.1 endpoint listed in REQUIRED_ENDPOINTS now ships a
# real handler — the 501 stub canary list is empty. If a future endpoint
# needs to land as a stub first, add it here.
async def test_v01_surface_has_no_remaining_501_stubs() -> None:
    """Belt-and-braces — guards against re-introducing stub responses
    silently. Each endpoint has its own positive-path test elsewhere."""
    spec = app.openapi()
    for path, methods in spec["paths"].items():
        if not (path == "/health" or path.startswith("/v1/")):
            continue
        for method, op in methods.items():
            if method.lower() not in {"get", "post", "put", "patch", "delete"}:
                continue
            responses = op.get("responses", {})
            assert "501" not in responses, (
                f"{method.upper()} {path} still ships the 501 stub response"
            )


async def test_validation_error_uses_standard_envelope(client: AsyncClient) -> None:
    resp = await client.post("/v1/auth/sms/send", json={"phone": "not-a-phone"})

    assert resp.status_code == 422
    payload = resp.json()
    assert payload["code"] == "VALIDATION_ERROR"
    assert payload["trace_id"]
    assert isinstance(payload["errors"], list)


async def test_health_still_works(client: AsyncClient) -> None:
    resp = await client.get("/health")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"


def test_sse_event_schemas_registered() -> None:
    """B generates types from openapi.yaml; the 4 SSE payloads + envelope must be present."""
    schemas = app.openapi()["components"]["schemas"]

    for name in (
        "OpponentDeltaEvent",
        "OpponentDoneEvent",
        "CoachHintEvent",
        "MetaEvent",
        "SseEventEnvelope",
    ):
        assert name in schemas, f"missing SSE schema: {name}"


def test_turns_response_uses_sse_envelope() -> None:
    """The /turns 200 response must reference SseEventEnvelope so codegen sees the discriminator."""
    spec = app.openapi()
    turn_op = spec["paths"]["/v1/sessions/{session_id}/turns"]["post"]
    schema_ref = turn_op["responses"]["200"]["content"]["application/json"]["schema"]["$ref"]

    assert schema_ref.endswith("/SseEventEnvelope")
