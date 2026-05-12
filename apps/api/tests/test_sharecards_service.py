"""`ShareCardService` orchestration tests (PR ③).

The renderer is real (Pillow); the storage is `LocalFilesystemStorage`
pointed at `tmp_path` so we don't bleed bytes between tests. Moderation
is the `NoopBackend` + log-only sink wiring from D5-A so caption gating
is exercised without DB.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from app.schemas.sharecards import SessionShareCardRequest
from app.services.moderation import LogOnlyEventSink, ModerationService, NoopBackend
from app.services.moderation.backend import ModerationBackend, ModerationBackendError
from app.services.moderation.types import Decision
from app.services.sharecards import (
    InMemorySessionScoreRepository,
    LocalFilesystemStorage,
    PillowShareCardRenderer,
    SessionCardData,
    ShareCardCaptionBlockedError,
    ShareCardNotFoundError,
    ShareCardService,
)
from app.services.sharecards.types import _DIMENSION_NAMES

SAMPLE_SCORE = SessionCardData(
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


class _BlockingBackend:
    """Always blocks — used to test caption gating."""

    name: str = "test_block"

    async def evaluate(self, content: str, context: str) -> Decision:
        _ = (content, context)
        return Decision(verdict="block", score=0.95, categories=("other",))


def _make_service(
    tmp_path: Path,
    *,
    score_repo: InMemorySessionScoreRepository,
    moderation_backend: ModerationBackend | None = None,
) -> ShareCardService:
    backend: ModerationBackend = moderation_backend or NoopBackend()
    moderation = ModerationService(backend=backend, event_sink=LogOnlyEventSink())
    return ShareCardService(
        renderer=PillowShareCardRenderer(),
        storage=LocalFilesystemStorage(
            root=tmp_path / "cards",
            public_base_url="http://test/sharecards",
        ),
        score_repo=score_repo,
        moderation=moderation,
        async_session_factory=None,  # audit row skipped in unit tests
        app_share_origin="https://careercoach.test",
    )


def _expected_dim_count() -> int:
    return len(_DIMENSION_NAMES)


async def test_create_session_card_returns_card_with_png_url(tmp_path: Path) -> None:
    repo = InMemorySessionScoreRepository({"ses_001": SAMPLE_SCORE})
    service = _make_service(tmp_path, score_repo=repo)

    resp = await service.create_session_card(
        session_id="ses_001",
        request=SessionShareCardRequest(),
        user_id="u_test",
        trace_id="trace-1",
    )

    assert resp.type == "session"
    assert resp.card_id.startswith("card_")
    assert resp.png_url == f"http://test/sharecards/{resp.card_id}.png"
    assert resp.share_links.save_local == resp.png_url
    assert resp.share_links.wechat.startswith("weixin://dl/share?card=card_")
    assert resp.pages == []


async def test_unknown_session_id_raises_not_found(tmp_path: Path) -> None:
    repo = InMemorySessionScoreRepository()
    service = _make_service(tmp_path, score_repo=repo)

    with pytest.raises(ShareCardNotFoundError):
        await service.create_session_card(
            session_id="ses_missing",
            request=SessionShareCardRequest(),
            user_id="u_test",
            trace_id="trace-2",
        )


async def test_png_file_actually_lands_on_disk(tmp_path: Path) -> None:
    repo = InMemorySessionScoreRepository({"ses_002": SAMPLE_SCORE})
    service = _make_service(tmp_path, score_repo=repo)

    resp = await service.create_session_card(
        session_id="ses_002",
        request=SessionShareCardRequest(),
        user_id="u_test",
        trace_id="trace-3",
    )

    written = (tmp_path / "cards" / f"{resp.card_id}.png").read_bytes()
    assert written.startswith(b"\x89PNG\r\n\x1a\n")
    assert len(written) > 1000  # a real 1080x1920 card is much bigger


async def test_user_caption_passes_moderation_and_appears_on_card(tmp_path: Path) -> None:
    repo = InMemorySessionScoreRepository({"ses_003": SAMPLE_SCORE})
    service = _make_service(tmp_path, score_repo=repo)

    resp = await service.create_session_card(
        session_id="ses_003",
        request=SessionShareCardRequest(user_caption_override="今天嘴硬了一把"),
        user_id="u_test",
        trace_id="trace-4",
    )

    # The captioned card differs from the default; smoke check via size.
    captioned_bytes = (tmp_path / "cards" / f"{resp.card_id}.png").read_bytes()
    plain_resp = await service.create_session_card(
        session_id="ses_003",
        request=SessionShareCardRequest(),
        user_id="u_test",
        trace_id="trace-4b",
    )
    plain_bytes = (tmp_path / "cards" / f"{plain_resp.card_id}.png").read_bytes()
    assert captioned_bytes != plain_bytes


async def test_blocked_caption_raises_caption_blocked_error(tmp_path: Path) -> None:
    repo = InMemorySessionScoreRepository({"ses_004": SAMPLE_SCORE})
    service = _make_service(tmp_path, score_repo=repo, moderation_backend=_BlockingBackend())

    with pytest.raises(ShareCardCaptionBlockedError) as excinfo:
        await service.create_session_card(
            session_id="ses_004",
            request=SessionShareCardRequest(user_caption_override="trash content"),
            user_id="u_test",
            trace_id="trace-5",
        )

    assert "other" in excinfo.value.categories


async def test_caption_skipped_when_not_provided(tmp_path: Path) -> None:
    """Empty/None caption must NOT call moderation — saves a round trip."""
    repo = InMemorySessionScoreRepository({"ses_005": SAMPLE_SCORE})

    class _ExplodeBackend:
        name = "explode"

        async def evaluate(self, content: str, context: str) -> Decision:
            _ = (content, context)
            raise ModerationBackendError("must not be called", backend=self.name)

    service = _make_service(tmp_path, score_repo=repo, moderation_backend=_ExplodeBackend())

    # Should NOT raise — moderation wasn't invoked.
    resp = await service.create_session_card(
        session_id="ses_005",
        request=SessionShareCardRequest(),
        user_id="u_test",
        trace_id="trace-6",
    )
    assert resp.card_id.startswith("card_")


def test_top_dimensions_count_matches_renderer_expectations() -> None:
    """Guards against a future PR shrinking `_DIMENSION_NAMES` and breaking layout."""
    assert _expected_dim_count() == 5
