"""End-to-end tests: minor JWT drives strict moderation through the
internal callers (turn / sharecard caption), not just `/moderation/check`.

A-4 added the strict tier and wired it into the external moderation
endpoint, but the sandbox-turn flow and the sharecard caption-override
flow have their own internal `moderation.check` calls. This file pins
that those internal callers also honor `is_minor` from the JWT.

The mechanic under test: a backend that returns `warn` should get
elevated to `block` for a minor JWT, which manifests as:
  * `POST /v1/sessions/{id}/turns` → 400 USER_INPUT_BLOCKED
  * `POST /v1/sharecards/session/{id}` with caption override → 400
    CAPTION_BLOCKED
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterator
from dataclasses import dataclass

import pytest
from app.main import app
from app.services.auth import mint_token
from app.services.moderation import (
    Decision,
    LogOnlyEventSink,
    ModerationService,
)
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


@dataclass
class _WarnBackend:
    """Backend that always returns `warn` — the elevation to `block`
    happens in the moderation service's strict tier, NOT here. Letting
    us prove the strict tier is what blocks the request (rather than
    the backend doing it on its own)."""

    name: str = "test_warn"

    async def evaluate(self, content: str, context: str) -> Decision:
        _ = (content, context)
        return Decision(verdict="warn", score=0.6, categories=("harassment",))


def _build_services() -> tuple[SessionService, TurnService]:
    session_repo = InMemorySessionRepository()
    turn_repo = InMemoryTurnRepository()
    score_repo = InMemorySessionScoreRepository()
    llm = _ScriptedProvider(
        roleplay="what is more important than work?",
        coach="SAFE: dodge\nAGGRESSIVE: stand firm\nHUMOR: pun",
        judge="VERDICT: guolu\nRATING: 70",
    )
    session_svc = SessionService(
        repository=session_repo,
        score_repo=score_repo,
        turn_repo=turn_repo,
        llm=llm,
    )
    moderation = ModerationService(
        backend=_WarnBackend(),
        event_sink=LogOnlyEventSink(),
    )
    turn_svc = TurnService(
        llm=llm,
        moderation=moderation,
        session_repo=session_repo,
        turn_repo=turn_repo,
    )
    return session_svc, turn_svc


@pytest.fixture
def warn_services() -> Iterator[None]:
    session_svc, turn_svc = _build_services()
    app.dependency_overrides[get_session_service] = lambda: session_svc
    app.dependency_overrides[get_turn_service] = lambda: turn_svc
    app.dependency_overrides[get_session_score_repository] = lambda: (
        InMemorySessionScoreRepository()
    )
    try:
        yield
    finally:
        for dep in (
            get_session_service,
            get_turn_service,
            get_session_score_repository,
        ):
            app.dependency_overrides.pop(dep, None)


def _bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
async def client() -> AsyncIterator[AsyncClient]:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


async def _create_session(client: AsyncClient, *, token: str) -> str:
    resp = await client.post(
        "/v1/sessions",
        headers=_bearer(token),
        json={
            "mode": "sandbox",
            "scenario_id": "sc_001",
            "persona_id": "p_hard",
            "user_goal": "保住周末",
        },
    )
    assert resp.status_code == 200, resp.text
    return resp.json()["session_id"]


# --- /turns -----------------------------------------------------------


async def test_minor_jwt_blocks_warn_content_on_turns(
    client: AsyncClient,
    warn_services: None,
) -> None:
    """The load-bearing E2E for A-5: a `warn` decision on a turn from
    a minor JWT must elevate to `block` and surface as
    USER_INPUT_BLOCKED, not silently pass through to the LLM."""
    _ = warn_services
    minor_token = mint_token(user_id="u_minor", persona_type="in_school", is_minor=True)
    session_id = await _create_session(client, token=minor_token)

    resp = await client.post(
        f"/v1/sessions/{session_id}/turns",
        headers=_bearer(minor_token),
        json={"content": "borderline content"},
    )

    assert resp.status_code == 400, resp.text
    body = resp.json()
    assert body["code"] == "USER_INPUT_BLOCKED"


async def test_adult_jwt_lets_warn_content_through_on_turns(
    client: AsyncClient,
    warn_services: None,
) -> None:
    """Sanity counterpart: same `warn` content, adult JWT, must NOT
    be elevated — confirms the strict tier is gated on `is_minor`
    and not just always-on."""
    _ = warn_services
    adult_token = mint_token(user_id="u_adult", persona_type="intern", is_minor=False)
    session_id = await _create_session(client, token=adult_token)

    resp = await client.post(
        f"/v1/sessions/{session_id}/turns",
        headers=_bearer(adult_token),
        json={"content": "borderline content"},
    )

    # `warn` is not a 4xx — the request proceeds and we get the SSE
    # stream. 200 is enough for this test; the stream contents are
    # owned by the turn-route's own test file.
    assert resp.status_code == 200, resp.text


# --- /sharecards/session/{id} caption override ------------------------


async def test_minor_jwt_blocks_warn_caption_on_session_card(
    client: AsyncClient,
    tmp_path,  # type: ignore[no-untyped-def]
) -> None:
    """The sharecard route runs the user's caption override through
    moderation. Same elevation: minor + warn → CAPTION_BLOCKED.

    Mirrors `test_sharecards_route.py`'s fixture style — local FS
    storage + Pillow renderer — so the only thing that differs from
    the happy-path test is the moderation backend (warn) + the JWT
    (minor)."""
    from app.services.sharecards import (
        InMemorySessionScoreRepository,
        LocalFilesystemStorage,
        PillowShareCardRenderer,
        SessionCardData,
        ShareCardService,
        get_sharecard_service,
    )

    sample = SessionCardData(
        scenario_title="周末加班",
        persona_title="老板小张",
        aura=8,
        logic=7,
        emotion=7,
        professionalism=8,
        goal_achieve=7,
        result="guolu",
        highlights="K likes how you held the line.",
    )
    score_repo = InMemorySessionScoreRepository({"ses_minor_test": sample})
    moderation = ModerationService(
        backend=_WarnBackend(),
        event_sink=LogOnlyEventSink(),
    )
    service = ShareCardService(
        renderer=PillowShareCardRenderer(),
        storage=LocalFilesystemStorage(
            root=tmp_path / "cards",
            public_base_url="http://test/sharecards",
        ),
        score_repo=score_repo,
        moderation=moderation,
        async_session_factory=None,
        app_share_origin="https://careercoach.test",
    )
    app.dependency_overrides[get_sharecard_service] = lambda: service

    try:
        minor_token = mint_token(user_id="u_minor", persona_type="in_school", is_minor=True)
        resp = await client.post(
            "/v1/sharecards/session/ses_minor_test",
            headers=_bearer(minor_token),
            json={
                "user_caption_override": "borderline caption",
                "include_qrcode": False,
            },
        )

        assert resp.status_code == 400, resp.text
        body = resp.json()
        assert body["code"] == "CAPTION_BLOCKED"
    finally:
        app.dependency_overrides.pop(get_sharecard_service, None)
