"""HTTP-layer tests for `POST /v1/sessions` and `POST /v1/sessions/{id}/end`.

We override `get_session_service` with a fresh in-memory wiring per
test so the singleton state from a previous test never leaks across
the boundary.

`/turns` stays 501 in PR 4a — the test for that ensures the contract
is "still 501" rather than accidentally working with a stale handler.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterator

import pytest
from app.llm import Message
from app.main import app
from app.services.auth import mint_token
from app.services.sessions import (
    InMemorySessionRepository,
    InMemoryTurnRepository,
    SessionService,
    get_session_service,
)
from app.services.sharecards.session_score import (
    InMemorySessionScoreRepository,
    get_session_score_repository,
)
from httpx import ASGITransport, AsyncClient

# A default Bearer token is attached to the `client` fixture so the
# vast majority of tests don't need to repeat the auth plumbing. Tests
# that probe unauth/bad-token branches use the bare `anon_client` below.
_DEFAULT_TEST_USER_ID = "u_default_test"


class _RouteStubLLM:
    """Same shape as the aggregator's `LLMProvider` Protocol — emits a
    canned summary so /end has a deterministic Score on the route."""

    name = "route_stub"

    async def stream_chat(
        self,
        messages: list[Message],
        *,
        temperature: float = 0.7,
        timeout: float = 30.0,
    ) -> AsyncIterator[str]:
        del messages, temperature, timeout
        yield (
            "AURA: 7\n"
            "LOGIC: 6\n"
            "EMOTION: 6\n"
            "PROFESSIONALISM: 7\n"
            "GOAL_ACHIEVE: 5\n"
            "HIGHLIGHTS: 整体表达稳\n"
            "FAILURES: 末段略软"
        )


@pytest.fixture
def service_override() -> Iterator[InMemorySessionScoreRepository]:
    """Wire a fresh SessionService backed by isolated in-memory stores.

    The score repo is also overridden so a sharecards route call could
    read what `/end` wrote — covering the cross-service seam end-to-end
    is exercised separately in test_sessions_service.py.
    """
    score_repo = InMemorySessionScoreRepository()
    service = SessionService(
        repository=InMemorySessionRepository(),
        score_repo=score_repo,
        turn_repo=InMemoryTurnRepository(),
        llm=_RouteStubLLM(),
    )
    app.dependency_overrides[get_session_service] = lambda: service
    app.dependency_overrides[get_session_score_repository] = lambda: score_repo
    try:
        yield score_repo
    finally:
        app.dependency_overrides.pop(get_session_service, None)
        app.dependency_overrides.pop(get_session_score_repository, None)


@pytest.fixture
async def client(
    service_override: InMemorySessionScoreRepository,
) -> AsyncIterator[AsyncClient]:
    _ = service_override
    token = mint_token(
        user_id=_DEFAULT_TEST_USER_ID,
        persona_type="intern",
        is_minor=False,
        # age_set so the compulsory age gate (PRD §1.5) doesn't fire —
        # these tests exercise session behavior, not the gate itself.
        age_set=True,
    )
    transport = ASGITransport(app=app)
    async with AsyncClient(
        transport=transport,
        base_url="http://test",
        headers={"Authorization": f"Bearer {token}"},
    ) as ac:
        yield ac


@pytest.fixture
async def anon_client(
    service_override: InMemorySessionScoreRepository,
) -> AsyncIterator[AsyncClient]:
    """Client without the default Authorization header — for tests that
    assert the 401 branch fires when no token is supplied."""
    _ = service_override
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


async def test_create_session_returns_200_with_session_id_and_opening_line(
    client: AsyncClient,
) -> None:
    resp = await client.post(
        "/v1/sessions",
        json={
            "mode": "sandbox",
            "scenario_id": "sc_001",
            "persona_id": "p_hard",
            "user_goal": "保住周末",
        },
    )

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["session_id"].startswith("ses_")
    assert body["opening_line"]  # non-empty
    assert isinstance(body["opening_line"], str)


async def test_create_session_validates_required_fields(client: AsyncClient) -> None:
    resp = await client.post("/v1/sessions", json={"mode": "sandbox"})
    assert resp.status_code == 422
    body = resp.json()
    assert body["code"] == "VALIDATION_ERROR"


async def test_create_session_validates_user_goal_length(client: AsyncClient) -> None:
    """user_goal is capped at 200 chars per schemas/sessions.py."""
    resp = await client.post(
        "/v1/sessions",
        json={
            "mode": "sandbox",
            "scenario_id": "sc_001",
            "persona_id": "p_hard",
            "user_goal": "x" * 201,
        },
    )
    assert resp.status_code == 422


async def test_create_session_rejects_invalid_mode(client: AsyncClient) -> None:
    resp = await client.post(
        "/v1/sessions",
        json={
            "mode": "telepathy",  # not in the Literal union
            "scenario_id": "sc_001",
            "persona_id": "p_hard",
            "user_goal": "试试",
        },
    )
    assert resp.status_code == 422


async def test_end_session_route_returns_score_with_5_dims(client: AsyncClient) -> None:
    created = await client.post(
        "/v1/sessions",
        json={
            "mode": "sandbox",
            "scenario_id": "sc_001",
            "persona_id": "p_hard",
            "user_goal": "保住周末",
        },
    )
    session_id = created.json()["session_id"]

    resp = await client.post(f"/v1/sessions/{session_id}/end")

    assert resp.status_code == 200, resp.text
    body = resp.json()
    for dim in ("aura", "logic", "emotion", "professionalism", "goal_achieve"):
        assert 0 <= body["score"][dim] <= 10
    assert body["score"]["result"] in {"shenfeng", "guolu", "fanche"}
    assert isinstance(body["weakness_updates"], list)


async def test_end_session_route_returns_404_for_unknown_session(
    client: AsyncClient,
) -> None:
    resp = await client.post("/v1/sessions/ses_never_existed/end")
    assert resp.status_code == 404
    body = resp.json()
    assert body["code"] == "NOT_FOUND"


async def test_create_session_with_valid_bearer_token_uses_jwt_user_id(
    client: AsyncClient,
) -> None:
    """When a real JWT lands in the Authorization header, the session
    must be attributed to that user — not the anonymous sentinel."""
    from app.services.auth import mint_token
    from app.services.sessions import get_session_service

    token = mint_token(user_id="u_route_test", persona_type="intern", is_minor=False, age_set=True)
    resp = await client.post(
        "/v1/sessions",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "mode": "sandbox",
            "scenario_id": "sc_001",
            "persona_id": "p_hard",
            "user_goal": "保住周末",
        },
    )

    assert resp.status_code == 200, resp.text
    session_id = resp.json()["session_id"]

    # Inspect the persisted SessionRecord to confirm the user_id was
    # actually carried through — fixture-installed service is async-fresh.
    service = app.dependency_overrides[get_session_service]()
    record = await service._repository.get(session_id)
    assert record is not None
    assert record.user_id == "u_route_test"


async def test_create_session_without_authorization_header_returns_401(
    anon_client: AsyncClient,
) -> None:
    """Hard auth: missing bearer token → 401 before the service runs."""
    resp = await anon_client.post(
        "/v1/sessions",
        json={
            "mode": "sandbox",
            "scenario_id": "sc_001",
            "persona_id": "p_hard",
            "user_goal": "保住周末",
        },
    )

    assert resp.status_code == 401
    body = resp.json()
    assert body["code"] == "UNAUTHORIZED"
    # Trace id must still flow through the error path so a 401 in
    # production is traceable in Sentry / Langfuse.
    assert "trace_id" in body


async def test_end_session_route_returns_409_when_ended_twice(client: AsyncClient) -> None:
    created = await client.post(
        "/v1/sessions",
        json={
            "mode": "sandbox",
            "scenario_id": "sc_001",
            "persona_id": "p_hard",
            "user_goal": "保住周末",
        },
    )
    session_id = created.json()["session_id"]
    first = await client.post(f"/v1/sessions/{session_id}/end")
    assert first.status_code == 200

    second = await client.post(f"/v1/sessions/{session_id}/end")
    assert second.status_code == 409
    assert second.json()["code"] == "ALREADY_ENDED"


# PR 4b removed the /turns 501 canary — SSE-driven /turns coverage
# lives in test_sessions_turns_route.py.
