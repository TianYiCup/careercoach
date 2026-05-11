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
    ("get", "/v1/scenarios"),
    ("post", "/v1/sessions"),
    ("post", "/v1/sessions/{session_id}/turns"),
    ("post", "/v1/sessions/{session_id}/end"),
    ("post", "/v1/moderation/check"),
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


@pytest.mark.parametrize(
    ("method", "path", "body"),
    [
        ("post", "/v1/auth/sms/send", {"phone": "13800138000"}),
        ("post", "/v1/auth/sms/verify", {"phone": "13800138000", "code": "123456"}),
        ("get", "/v1/scenarios", None),
        (
            "post",
            "/v1/sessions",
            {
                "mode": "sandbox",
                "scenario_id": "sc_001",
                "persona_id": "p_hard",
                "user_goal": "保住周末",
            },
        ),
        ("post", "/v1/sessions/ses_x/turns", {"content": "test"}),
        ("post", "/v1/sessions/ses_x/end", None),
        (
            "post",
            "/v1/moderation/check",
            {
                "content": "测试",
                "context": "user_input",
                "user_id": "u_test",
            },
        ),
    ],
)
async def test_stubs_return_501_with_envelope(
    client: AsyncClient, method: str, path: str, body: dict[str, object] | None
) -> None:
    if method == "get":
        resp = await client.get(path)
    else:
        resp = await client.post(path, json=body)

    assert resp.status_code == 501, resp.text
    payload = resp.json()
    assert payload["code"] == "NOT_IMPLEMENTED"
    assert payload["message"]
    assert payload["trace_id"]


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
