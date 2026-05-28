"""HTTP-level tests for `POST /v1/sessions/{id}/turns`.

Covers the validate-then-stream split: 4xx errors come back as normal
HTTP responses with the standard envelope; only valid turns open the
SSE stream. The SSE wire format itself (event:/data:/blank-line) is
parsed back into frames so we assert against the exact bytes a real
client receives.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator, Iterator

import pytest
from app.main import app
from app.schemas.moderation import RedirectResource
from app.services.auth import mint_token
from app.services.moderation import LogOnlyEventSink, ModerationService, NoopBackend
from app.services.moderation.types import Decision
from app.services.sessions import (
    InMemorySessionRepository,
    InMemoryTurnRepository,
    SessionService,
    TurnService,
    get_session_service,
    get_turn_service,
)
from app.services.sharecards.session_score import (
    InMemorySessionScoreRepository,
    get_session_score_repository,
)
from httpx import ASGITransport, AsyncClient

from tests.test_sessions_turn_service import _ScriptedProvider


def _build_services(
    *,
    block_moderation: bool = False,
    redirect_moderation: bool = False,
) -> tuple[SessionService, TurnService]:
    session_repo = InMemorySessionRepository()
    turn_repo = InMemoryTurnRepository()
    score_repo = InMemorySessionScoreRepository()
    # The same scripted LLM serves both /turns and the /end summarizer
    # in these route tests — keyword routing in `_ScriptedProvider`
    # picks the right canned reply by system-prompt content.
    llm = _ScriptedProvider(
        roleplay="什么安排比工作还重要？",
        coach="SAFE: 反问 deadline\nAGGRESSIVE: 引用劳动法\nHUMOR: 跟床约了不能放鸽子",
        judge="VERDICT: guolu\nRATING: 70",
    )
    session_svc = SessionService(
        repository=session_repo,
        score_repo=score_repo,
        turn_repo=turn_repo,
        llm=llm,
    )

    moderation: ModerationService
    if block_moderation:

        class _BlockingBackend:
            name = "test_block"

            async def evaluate(self, content: str, context: str) -> Decision:
                _ = (content, context)
                return Decision(verdict="block", score=0.99, categories=("other",))

        moderation = ModerationService(backend=_BlockingBackend(), event_sink=LogOnlyEventSink())
    elif redirect_moderation:

        class _RedirectingBackend:
            name = "test_redirect"

            async def evaluate(self, content: str, context: str) -> Decision:
                _ = (content, context)
                return Decision(
                    verdict="redirect",
                    score=0.99,
                    categories=("self_harm",),
                    redirect_resource=RedirectResource(
                        title="心理援助 24h 热线",
                        url="tel:010-82951332",
                    ),
                )

        moderation = ModerationService(backend=_RedirectingBackend(), event_sink=LogOnlyEventSink())
    else:
        moderation = ModerationService(backend=NoopBackend(), event_sink=LogOnlyEventSink())

    turn_svc = TurnService(
        llm=llm,
        moderation=moderation,
        session_repo=session_repo,
        turn_repo=turn_repo,
    )
    return session_svc, turn_svc


@pytest.fixture
def service_override() -> Iterator[None]:
    session_svc, turn_svc = _build_services()
    app.dependency_overrides[get_session_service] = lambda: session_svc
    app.dependency_overrides[get_turn_service] = lambda: turn_svc
    # Pre-bind the score repo too so /end + sharecards stay coherent
    # if a later test reaches in.
    score_repo = InMemorySessionScoreRepository()
    app.dependency_overrides[get_session_score_repository] = lambda: score_repo
    try:
        yield
    finally:
        for dep in (
            get_session_service,
            get_turn_service,
            get_session_score_repository,
        ):
            app.dependency_overrides.pop(dep, None)


@pytest.fixture
def blocking_service_override() -> Iterator[None]:
    session_svc, turn_svc = _build_services(block_moderation=True)
    app.dependency_overrides[get_session_service] = lambda: session_svc
    app.dependency_overrides[get_turn_service] = lambda: turn_svc
    try:
        yield
    finally:
        for dep in (get_session_service, get_turn_service):
            app.dependency_overrides.pop(dep, None)


@pytest.fixture
def redirecting_service_override() -> Iterator[None]:
    session_svc, turn_svc = _build_services(redirect_moderation=True)
    app.dependency_overrides[get_session_service] = lambda: session_svc
    app.dependency_overrides[get_turn_service] = lambda: turn_svc
    try:
        yield
    finally:
        for dep in (get_session_service, get_turn_service):
            app.dependency_overrides.pop(dep, None)


@pytest.fixture
async def client() -> AsyncIterator[AsyncClient]:
    """Default-authenticated client — every route here is auth-gated,
    so the fixture mints a token once and attaches it as a default
    header. Per-test calls still win on header conflicts (the existing
    `headers={"Authorization": ...}` overrides keep working)."""
    token = mint_token(
        user_id="u_default_test",
        persona_type="intern",
        is_minor=False,
        age_set=True,  # bypass A-6 age gate; gate tests live in test_age_gate.py
    )
    transport = ASGITransport(app=app)
    async with AsyncClient(
        transport=transport,
        base_url="http://test",
        headers={"Authorization": f"Bearer {token}"},
    ) as ac:
        yield ac


def _parse_sse(body: str) -> list[tuple[str, dict[str, object]]]:
    """Parse `event: <name>\\ndata: <json>\\n\\n` blocks into tuples."""
    frames: list[tuple[str, dict[str, object]]] = []
    for block in body.split("\n\n"):
        if not block.strip():
            continue
        event = ""
        data_line = ""
        for line in block.split("\n"):
            if line.startswith("event: "):
                event = line.removeprefix("event: ")
            elif line.startswith("data: "):
                data_line = line.removeprefix("data: ")
        if event and data_line:
            frames.append((event, json.loads(data_line)))
    return frames


async def _create_session(client: AsyncClient) -> str:
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
    return resp.json()["session_id"]


# --- 4xx pre-stream errors ---


async def test_turns_returns_404_for_unknown_session(
    client: AsyncClient, service_override: None
) -> None:
    _ = service_override
    resp = await client.post(
        "/v1/sessions/ses_never/turns",
        json={"content": "hello"},
    )
    assert resp.status_code == 404
    body = resp.json()
    assert body["code"] == "NOT_FOUND"


async def test_turns_returns_400_when_moderation_blocks(
    client: AsyncClient, blocking_service_override: None
) -> None:
    _ = blocking_service_override
    session_id = await _create_session(client)
    resp = await client.post(
        f"/v1/sessions/{session_id}/turns",
        json={"content": "我想自杀"},
    )
    assert resp.status_code == 400
    body = resp.json()
    assert body["code"] == "USER_INPUT_BLOCKED"


async def test_turns_redirect_emits_moderation_frame(
    client: AsyncClient, redirecting_service_override: None
) -> None:
    """Self-harm input → verdict=redirect: H-1 streams a single
    `moderation` frame carrying the crisis resource (PRD §3.0.5 A) and
    no opponent reply. The response is still a 200 SSE stream — the
    frontend's `moderation` SSE handler renders the help card."""
    _ = redirecting_service_override
    session_id = await _create_session(client)
    resp = await client.post(
        f"/v1/sessions/{session_id}/turns",
        json={"content": "我不想活了"},
    )
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/event-stream")
    frames = _parse_sse(resp.text)
    events = [name for name, _ in frames]
    assert events == ["moderation"]
    _, data = frames[0]
    assert data["verdict"] == "redirect"
    assert data["redirect_resource"]["title"] == "心理援助 24h 热线"
    assert data["redirect_resource"]["url"] == "tel:010-82951332"
    assert "opponent.delta" not in events


async def test_turns_returns_409_for_ended_session(
    client: AsyncClient, service_override: None
) -> None:
    _ = service_override
    session_id = await _create_session(client)
    end_resp = await client.post(f"/v1/sessions/{session_id}/end")
    assert end_resp.status_code == 200

    resp = await client.post(
        f"/v1/sessions/{session_id}/turns",
        json={"content": "hi"},
    )
    assert resp.status_code == 409
    body = resp.json()
    assert body["code"] == "ALREADY_ENDED"


async def test_turns_rejects_empty_content(client: AsyncClient, service_override: None) -> None:
    _ = service_override
    session_id = await _create_session(client)
    resp = await client.post(
        f"/v1/sessions/{session_id}/turns",
        json={"content": ""},
    )
    assert resp.status_code == 422
    assert resp.json()["code"] == "VALIDATION_ERROR"


# --- SSE happy path ---


async def test_turns_emits_sse_stream_with_correct_content_type(
    client: AsyncClient, service_override: None
) -> None:
    _ = service_override
    session_id = await _create_session(client)
    resp = await client.post(
        f"/v1/sessions/{session_id}/turns",
        json={"content": "老板我周末有事"},
    )
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/event-stream")


async def test_turns_emits_four_event_types_in_expected_order(
    client: AsyncClient, service_override: None
) -> None:
    _ = service_override
    session_id = await _create_session(client)
    resp = await client.post(
        f"/v1/sessions/{session_id}/turns",
        json={"content": "老板我周末有事"},
    )
    frames = _parse_sse(resp.text)
    events = [name for name, _ in frames]

    assert events.count("opponent.delta") >= 1
    assert events.count("opponent.done") == 1
    assert events.count("coach.hint") == 1
    assert events.count("meta") == 1
    # Order matches PRD §7.4.
    done_idx = events.index("opponent.done")
    coach_idx = events.index("coach.hint")
    meta_idx = events.index("meta")
    assert events.index("opponent.delta") < done_idx
    assert done_idx < coach_idx < meta_idx
    assert meta_idx == len(events) - 1


async def test_turns_delta_concatenates_to_done_full_text(
    client: AsyncClient, service_override: None
) -> None:
    _ = service_override
    session_id = await _create_session(client)
    resp = await client.post(
        f"/v1/sessions/{session_id}/turns",
        json={"content": "hi"},
    )
    frames = _parse_sse(resp.text)
    delta_text = "".join(data["text"] for name, data in frames if name == "opponent.delta")
    done_frame = next(data for name, data in frames if name == "opponent.done")
    assert delta_text == done_frame["full_text"]
    assert isinstance(done_frame["turn_id"], str)
    assert str(done_frame["turn_id"]).startswith("t_")


async def test_turns_coach_hint_carries_three_tones(
    client: AsyncClient, service_override: None
) -> None:
    _ = service_override
    session_id = await _create_session(client)
    resp = await client.post(
        f"/v1/sessions/{session_id}/turns",
        json={"content": "hi"},
    )
    frames = _parse_sse(resp.text)
    hint = next(data for name, data in frames if name == "coach.hint")
    assert hint == {
        "safe": "反问 deadline",
        "aggressive": "引用劳动法",
        "humor": "跟床约了不能放鸽子",
        # PR-L8: the coach frame now carries an optional strategy read.
        # The route fixture's scripted coach output has no STRATEGY block,
        # so it parses to None — the three tones are unaffected.
        "strategy": None,
    }


async def test_turns_meta_carries_turn_counters(
    client: AsyncClient, service_override: None
) -> None:
    _ = service_override
    session_id = await _create_session(client)
    resp = await client.post(
        f"/v1/sessions/{session_id}/turns",
        json={"content": "hi"},
    )
    frames = _parse_sse(resp.text)
    meta = next(data for name, data in frames if name == "meta")
    assert meta["turns_used"] == 1
    # MAX_TURNS_PER_SESSION = 30.
    assert meta["turns_left"] == 29


async def test_turns_openapi_response_still_references_sse_envelope() -> None:
    """The /turns 200 response shape contract — B's codegen relies on it."""
    spec = app.openapi()
    response_200 = spec["paths"]["/v1/sessions/{session_id}/turns"]["post"]["responses"]["200"]
    schema_ref = response_200["content"]["application/json"]["schema"]["$ref"]
    assert schema_ref.endswith("/SseEventEnvelope")
