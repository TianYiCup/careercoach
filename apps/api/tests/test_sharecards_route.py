"""Route-level smoke for `POST /v1/sharecards/session/{session_id}`.

We override the default `ShareCardService` with one wired to a tmp dir
storage + an in-memory score repo so the route flow is exercised
without DB or persistent disk.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterator
from pathlib import Path

import pytest
from app.main import app
from app.services.moderation import LogOnlyEventSink, ModerationService, NoopBackend
from app.services.moderation.types import Decision
from app.services.sharecards import (
    InMemorySessionScoreRepository,
    LocalFilesystemStorage,
    PillowShareCardRenderer,
    SessionCardData,
    ShareCardService,
    get_sharecard_service,
)
from httpx import ASGITransport, AsyncClient

_SAMPLE_SCORE = SessionCardData(
    scenario_title="Negotiate Saturday with Boss",
    persona_title="Hard-line Boss",
    aura=8,
    logic=7,
    emotion=6,
    professionalism=7,
    goal_achieve=9,
    result="shenfeng",
    highlights="K likes how you held the line.",
)


@pytest.fixture
def service_override(tmp_path: Path) -> Iterator[InMemorySessionScoreRepository]:
    score_repo = InMemorySessionScoreRepository({"ses_known": _SAMPLE_SCORE})
    moderation = ModerationService(backend=NoopBackend(), event_sink=LogOnlyEventSink())
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
        yield score_repo
    finally:
        app.dependency_overrides.pop(get_sharecard_service, None)


@pytest.fixture
async def client(
    service_override: InMemorySessionScoreRepository,
) -> AsyncIterator[AsyncClient]:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


async def test_session_card_route_returns_200_for_known_session(client: AsyncClient) -> None:
    resp = await client.post(
        "/v1/sharecards/session/ses_known",
        json={"include_qrcode": False},
    )

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["type"] == "session"
    assert body["card_id"].startswith("card_")
    assert body["png_url"].startswith("http://test/sharecards/")
    assert body["share_links"]["save_local"] == body["png_url"]
    assert body["share_links"]["wechat"].startswith("weixin://dl/share?card=")
    assert body["pages"] == []


async def test_session_card_route_returns_404_for_unknown_session(client: AsyncClient) -> None:
    resp = await client.post(
        "/v1/sharecards/session/ses_missing",
        json={"include_qrcode": False},
    )

    assert resp.status_code == 404
    body = resp.json()
    assert body["code"] == "NOT_FOUND"
    assert "ses_missing" in body["message"]


async def test_session_card_route_rejects_long_caption(client: AsyncClient) -> None:
    """81-char caption hits the schema validator before the service runs."""
    resp = await client.post(
        "/v1/sharecards/session/ses_known",
        json={
            "include_qrcode": False,
            "user_caption_override": "x" * 81,
        },
    )

    assert resp.status_code == 422
    assert resp.json()["code"] == "VALIDATION_ERROR"


async def test_caption_blocked_by_moderation_returns_400(
    client: AsyncClient,
    tmp_path: Path,
) -> None:
    """Swap in a blocking backend just for this test."""

    class _BlockingBackend:
        name = "test_block"

        async def evaluate(self, content: str, context: str) -> Decision:
            _ = (content, context)
            return Decision(verdict="block", score=0.99, categories=("other",))

    score_repo = InMemorySessionScoreRepository({"ses_known": _SAMPLE_SCORE})
    moderation = ModerationService(backend=_BlockingBackend(), event_sink=LogOnlyEventSink())
    blocking_service = ShareCardService(
        renderer=PillowShareCardRenderer(),
        storage=LocalFilesystemStorage(
            root=tmp_path / "cards-block",
            public_base_url="http://test/sharecards",
        ),
        score_repo=score_repo,
        moderation=moderation,
        async_session_factory=None,
    )
    app.dependency_overrides[get_sharecard_service] = lambda: blocking_service
    try:
        resp = await client.post(
            "/v1/sharecards/session/ses_known",
            json={
                "include_qrcode": False,
                "user_caption_override": "trash content",
            },
        )
    finally:
        # The outer fixture re-installs the noop override on exit.
        pass

    assert resp.status_code == 400
    body = resp.json()
    assert body["code"] == "CAPTION_BLOCKED"
    assert "other" in body["message"]


async def test_session_card_response_locked_in_openapi() -> None:
    spec = app.openapi()
    op_spec = spec["paths"]["/v1/sharecards/session/{session_id}"]["post"]
    schema_ref = op_spec["responses"]["200"]["content"]["application/json"]["schema"]["$ref"]
    assert schema_ref.endswith("/ShareCardResponse")


async def test_session_card_route_echoes_trace_id_to_log(client: AsyncClient) -> None:
    """Smoke that the x-request-id header is accepted (200 still returns)."""
    resp = await client.post(
        "/v1/sharecards/session/ses_known",
        headers={"x-request-id": "trace-from-header-001"},
        json={"include_qrcode": True},
    )
    assert resp.status_code == 200


async def test_weekly_card_route_returns_200_with_empty_user(client: AsyncClient) -> None:
    """A user with no sessions in the requested window still gets a
    well-formed weekly card — the renderer flips to "no practice"
    copy rather than 404ing."""
    resp = await client.post(
        "/v1/sharecards/weekly",
        json={"include_qrcode": False, "week_offset": 0},
    )

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["type"] == "weekly"
    assert body["card_id"].startswith("card_")
    assert body["png_url"].endswith(".png")
    assert body["pages"] == []
    assert body["share_links"]["save_local"] == body["png_url"]


async def test_weekly_card_route_validates_week_offset_lower_bound(
    client: AsyncClient,
) -> None:
    """week_offset is bounded to [-12, 0] in the schema."""
    resp = await client.post(
        "/v1/sharecards/weekly",
        json={"include_qrcode": False, "week_offset": -13},
    )
    assert resp.status_code == 422


async def test_weekly_card_route_validates_week_offset_upper_bound(
    client: AsyncClient,
) -> None:
    resp = await client.post(
        "/v1/sharecards/weekly",
        json={"include_qrcode": False, "week_offset": 1},
    )
    assert resp.status_code == 422


async def test_wrapped_card_route_returns_200_with_six_pages(client: AsyncClient) -> None:
    resp = await client.post(
        "/v1/sharecards/wrapped/year/2026",
        json={"include_qrcode": True},
    )

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["type"] == "wrapped"
    assert body["card_id"].startswith("card_")
    # Schema invariant: pages[0] doubles as the OG cover via png_url.
    assert body["png_url"] == body["pages"][0]
    assert len(body["pages"]) == 6
    for url in body["pages"]:
        assert url.endswith(".png")


async def test_wrapped_card_route_rejects_out_of_range_year(client: AsyncClient) -> None:
    resp = await client.post(
        "/v1/sharecards/wrapped/year/1999",
        json={"include_qrcode": True},
    )
    assert resp.status_code == 422
