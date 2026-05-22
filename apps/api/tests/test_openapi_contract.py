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
    ("post", "/v1/copilot/sessions"),
    ("post", "/v1/review/uploads"),
    ("get", "/v1/review/uploads/{upload_id}"),
    # A-42: ops-only rollup, gated by X-Ops-Token (A-41). Listed
    # here so the openapi contract test stays a deny-by-default
    # gate — every new endpoint surfaces on B's side of the
    # contract, even ones B never calls.
    ("get", "/v1/ops/token-cost"),
    # A-43: ops-only moderation tail. Same gate, same rationale.
    ("get", "/v1/ops/moderation-events"),
    # A-44: ops-only moderation rate stats. Same gate.
    ("get", "/v1/ops/moderation-stats"),
    # A-45: ops-only per-day token spend time series. Same gate.
    ("get", "/v1/ops/token-cost-daily"),
    # A-46: ops-only LLM call tail (drill-down companion). Same gate.
    ("get", "/v1/ops/llm-calls"),
    # R3-1: daily mood check-in (PRD §7.11). Plain JWT auth, no LLM path.
    ("post", "/v1/vibe/today"),
    # R3-2: practice-streak counter (PRD §7.11). Read-only, plain JWT.
    ("get", "/v1/streak"),
    # R3-3: weakness profile (PRD §7.7, US-C3). Read-only, plain JWT.
    ("get", "/v1/users/me/weaknesses"),
    # R3-4: custom scenario creation (PRD §7.3, US-A1). Plain JWT.
    ("post", "/v1/scenarios/custom"),
    # US-A2: opponent persona catalog (PRD §7.3). Static, read-only, no auth.
    ("get", "/v1/personas"),
}

# Endpoints intentionally shipped as 501 stubs so the compliance gates
# (`require_adult` / `require_age_set`) attach to the route *before*
# the business logic lands. Once the real handler ships, the
# corresponding entry here must be removed alongside the 501 row in
# `responses=`. See `docs/b-side-review-2026-05-15/`.
#
# Empty as of A-15 — both copilot and review POST routes now ship
# real handlers. The paired test
# `test_stub_allowed_endpoints_actually_ship_501` accepts an empty
# set as a no-op (its `for` loop simply doesn't iterate).
STUB_ALLOWED_ENDPOINTS: set[tuple[str, str]] = set()


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


async def test_v01_surface_has_no_unsanctioned_501_stubs() -> None:
    """Belt-and-braces — guards against re-introducing stub responses
    silently. The canary set is `STUB_ALLOWED_ENDPOINTS`; every other
    v0.x endpoint must have a real handler with no 501 row."""
    spec = app.openapi()
    for path, methods in spec["paths"].items():
        if not (path == "/health" or path.startswith("/v1/")):
            continue
        for method, op in methods.items():
            if method.lower() not in {"get", "post", "put", "patch", "delete"}:
                continue
            if (method.lower(), path) in STUB_ALLOWED_ENDPOINTS:
                continue
            responses = op.get("responses", {})
            assert "501" not in responses, (
                f"{method.upper()} {path} ships a 501 stub response but is not "
                "in STUB_ALLOWED_ENDPOINTS — either remove the stub or add it "
                "to the allowed list with a justification."
            )


def test_stub_allowed_endpoints_actually_ship_501() -> None:
    """The other half of the canary — every endpoint we *say* is a
    stub must really be a stub. Catches the case where someone wires
    a real handler but forgets to drop the entry from
    `STUB_ALLOWED_ENDPOINTS`, which would silently keep the 501
    row in the spec and confuse B's codegen."""
    spec = app.openapi()
    for method, path in STUB_ALLOWED_ENDPOINTS:
        op = spec["paths"][path][method]
        assert "501" in op.get("responses", {}), (
            f"{method.upper()} {path} is listed as a stub but no longer "
            "advertises 501 — drop it from STUB_ALLOWED_ENDPOINTS."
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
