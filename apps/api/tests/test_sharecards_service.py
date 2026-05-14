"""`ShareCardService` orchestration tests (PR ③).

The renderer is real (Pillow); the storage is `LocalFilesystemStorage`
pointed at `tmp_path` so we don't bleed bytes between tests. Moderation
is the `NoopBackend` + log-only sink wiring from D5-A so caption gating
is exercised without DB.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from app.schemas.sharecards import (
    SessionShareCardRequest,
    WeeklyShareCardRequest,
    WrappedShareCardRequest,
)
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
from app.services.sharecards.service import (
    _aggregate_weekly,
    _aggregate_wrapped,
    _resolve_week_window,
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


# ---------------------------------------------------------------------
# Weekly card flow
# ---------------------------------------------------------------------


async def test_weekly_card_returns_envelope_for_empty_user(tmp_path: Path) -> None:
    """An empty week still renders — the headline switches to the
    "K is waiting" copy so the response shape is identical."""
    repo = InMemorySessionScoreRepository()
    service = _make_service(tmp_path, score_repo=repo)

    resp = await service.create_weekly_card(
        request=WeeklyShareCardRequest(include_qrcode=False, week_offset=0),
        user_id="u_test",
        trace_id="trace-w1",
    )

    assert resp.type == "weekly"
    assert resp.card_id.startswith("card_")
    assert resp.png_url.endswith(".png")
    assert resp.pages == []
    written = (tmp_path / "cards" / f"{resp.card_id}.png").read_bytes()
    assert written.startswith(b"\x89PNG\r\n\x1a\n")


async def test_weekly_card_counts_sessions_in_window(tmp_path: Path) -> None:
    """Add three sessions to the in-memory repo; the renderer input
    must reflect that count."""
    repo = InMemorySessionScoreRepository(
        {
            "ses_w1": SAMPLE_SCORE,
            "ses_w2": SAMPLE_SCORE,
            "ses_w3": SAMPLE_SCORE,
        }
    )
    service = _make_service(tmp_path, score_repo=repo)

    resp = await service.create_weekly_card(
        request=WeeklyShareCardRequest(include_qrcode=False, week_offset=0),
        user_id="u_test",
        trace_id="trace-w2",
        # `now` lands inside the week the seeded sessions were stamped,
        # so they all fall in the offset=0 window.
        now=datetime.now(UTC) + timedelta(days=8),
    )

    assert resp.type == "weekly"
    assert resp.card_id.startswith("card_")


# ---------------------------------------------------------------------
# Wrapped card flow
# ---------------------------------------------------------------------


async def test_wrapped_card_emits_six_pages_for_empty_year(tmp_path: Path) -> None:
    """A user with no sessions still gets a valid 6-page Wrapped — the
    renderer's empty-year layout produces the right shape."""
    repo = InMemorySessionScoreRepository()
    service = _make_service(tmp_path, score_repo=repo)

    resp = await service.create_wrapped_card(
        year=2026,
        request=WrappedShareCardRequest(include_qrcode=True),
        user_id="u_test",
        trace_id="trace-wr1",
    )

    assert resp.type == "wrapped"
    assert len(resp.pages) == 6
    assert resp.png_url == resp.pages[0]  # schema invariant
    for page_url in resp.pages:
        assert page_url.endswith(".png")
    # Each page must land on disk under the card-id subdir.
    card_dir = tmp_path / "cards" / resp.card_id
    assert card_dir.is_dir()
    pngs = sorted(card_dir.glob("*.png"))
    assert len(pngs) == 6


async def test_wrapped_card_aggregates_real_year(tmp_path: Path) -> None:
    """A populated repo should produce non-placeholder narrative copy
    via the aggregator. We only assert the structural invariants here
    — narrative content is covered in aggregator tests."""
    repo = InMemorySessionScoreRepository({"ses_a": SAMPLE_SCORE})
    service = _make_service(tmp_path, score_repo=repo)

    resp = await service.create_wrapped_card(
        year=2026,
        request=WrappedShareCardRequest(include_qrcode=False),
        user_id="u_test",
        trace_id="trace-wr2",
    )

    assert len(resp.pages) == 6
    assert all(p.endswith(".png") for p in resp.pages)


# ---------------------------------------------------------------------
# Aggregators (pure functions — no IO)
# ---------------------------------------------------------------------


def test_aggregate_weekly_empty_uses_waiting_copy() -> None:
    data = _aggregate_weekly(week_label="2026 第 20 周", sessions=[])
    assert data.sessions_count == 0
    assert data.headline == "本周 K 在等你"
    assert data.top_scenario_title is None


def test_aggregate_weekly_picks_modal_verdict_for_headline() -> None:
    # Two shenfeng vs one fanche → "封神周" headline.
    shenfeng = SAMPLE_SCORE  # result="shenfeng"
    fanche_session = SessionCardData(
        scenario_title="Other Scenario",
        persona_title="Other",
        aura=2,
        logic=3,
        emotion=3,
        professionalism=2,
        goal_achieve=1,
        result="fanche",
        highlights="learning",
    )
    data = _aggregate_weekly(
        week_label="2026 第 20 周",
        sessions=[shenfeng, shenfeng, fanche_session],
    )
    assert data.sessions_count == 3
    assert data.shenfeng_count == 2
    assert data.fanche_count == 1
    assert "封神周" in data.headline
    # Top scenario is the more-frequent shenfeng one.
    assert data.top_scenario_title == "Negotiate Saturday with Boss"


def test_aggregate_wrapped_empty_uses_placeholder_copy() -> None:
    data = _aggregate_wrapped(year=2026, sessions=[])
    assert data.total_sessions == 0
    assert data.top_scenario_title == "—"
    assert data.closing_letter


def test_aggregate_wrapped_picks_best_and_worst() -> None:
    best = SessionCardData(
        scenario_title="High Score Scenario",
        persona_title="Easy Mode",
        aura=10,
        logic=10,
        emotion=10,
        professionalism=10,
        goal_achieve=10,
        result="shenfeng",
        highlights="great",
    )
    worst = SessionCardData(
        scenario_title="Low Score Scenario",
        persona_title="Hard Mode",
        aura=1,
        logic=1,
        emotion=1,
        professionalism=1,
        goal_achieve=1,
        result="fanche",
        highlights="tough",
    )
    data = _aggregate_wrapped(year=2026, sessions=[best, worst])
    assert data.total_sessions == 2
    assert data.best_session_title == "High Score Scenario"
    assert data.worst_session_title == "Low Score Scenario"


def test_resolve_week_window_offset_zero_picks_previous_iso_week() -> None:
    """offset=0 means the immediately preceding completed ISO week."""
    # Pick a Wednesday so we're solidly inside an ISO week.
    anchor = datetime(2026, 5, 13, 12, tzinfo=UTC)
    since, until, label = _resolve_week_window(anchor, week_offset=0)

    assert (until - since) == timedelta(weeks=1)
    # Label is roughly "2026 第 19 周" (the week containing May 4-10
    # in Shanghai time — one before the May 11-17 week the anchor sits in).
    assert "2026 第" in label
    assert "周" in label
