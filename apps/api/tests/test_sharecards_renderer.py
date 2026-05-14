"""PR ② tests for `PillowShareCardRenderer`.

We assert structural properties of the rendered PNG (dimensions,
format, basic content presence) rather than pixel-perfect equality —
font fallback and Pillow version drift make byte-level baselines too
flaky for v0. PR ③'s storage tests pin the round trip.
"""

from __future__ import annotations

from io import BytesIO
from pathlib import Path

import pytest
from app.services.sharecards import (
    CARD_HEIGHT,
    CARD_WIDTH,
    PillowShareCardRenderer,
    SessionCardData,
    ShareCardRenderer,
    ShareCardRendererError,
)
from app.services.sharecards.types import WeeklyCardData, WrappedCardData
from PIL import Image


def _sample_data(**overrides: object) -> SessionCardData:
    defaults: dict[str, object] = {
        "scenario_title": "Negotiate Saturday with Boss",
        "persona_title": "Hard-line Boss",
        "aura": 8,
        "logic": 7,
        "emotion": 6,
        "professionalism": 7,
        "goal_achieve": 8,
        "result": "shenfeng",
        "highlights": "K likes how you held the line.",
    }
    defaults.update(overrides)
    return SessionCardData(**defaults)  # type: ignore[arg-type]


def test_session_card_data_rejects_score_out_of_range() -> None:
    with pytest.raises(ValueError, match="aura"):
        _sample_data(aura=11)
    with pytest.raises(ValueError, match="logic"):
        _sample_data(logic=-1)


def test_session_card_data_rejects_blank_titles() -> None:
    with pytest.raises(ValueError, match="scenario_title"):
        _sample_data(scenario_title="")
    with pytest.raises(ValueError, match="persona_title"):
        _sample_data(persona_title="")


def test_overall_score_is_one_decimal_average() -> None:
    data = _sample_data(aura=8, logic=7, emotion=6, professionalism=7, goal_achieve=8)
    assert data.overall_score == pytest.approx(7.2)


def test_top_dimensions_returns_three_highest_descending() -> None:
    data = _sample_data(aura=9, logic=4, emotion=8, professionalism=3, goal_achieve=7)
    labels = [label for label, _ in data.top_dimensions]
    scores = [score for _, score in data.top_dimensions]
    assert labels == ["气场", "情绪", "达成"]
    assert scores == [9, 8, 7]


def test_renderer_implements_protocol() -> None:
    renderer = PillowShareCardRenderer()
    assert isinstance(renderer, ShareCardRenderer)
    assert renderer.name == "pillow"


def test_render_produces_png_of_expected_dimensions() -> None:
    renderer = PillowShareCardRenderer()
    png = renderer.render_session_card(_sample_data())

    image = Image.open(BytesIO(png))
    assert image.format == "PNG"
    assert image.size == (CARD_WIDTH, CARD_HEIGHT)


@pytest.mark.parametrize("result", ["shenfeng", "guolu", "fanche"])
def test_render_handles_all_three_result_verdicts(result: str) -> None:
    renderer = PillowShareCardRenderer()
    png = renderer.render_session_card(_sample_data(result=result))

    image = Image.open(BytesIO(png))
    assert image.size == (CARD_WIDTH, CARD_HEIGHT)


def test_render_rejects_unknown_result() -> None:
    renderer = PillowShareCardRenderer()
    bad = SessionCardData.__new__(SessionCardData)
    object.__setattr__(bad, "scenario_title", "x")
    object.__setattr__(bad, "persona_title", "y")
    object.__setattr__(bad, "aura", 5)
    object.__setattr__(bad, "logic", 5)
    object.__setattr__(bad, "emotion", 5)
    object.__setattr__(bad, "professionalism", 5)
    object.__setattr__(bad, "goal_achieve", 5)
    object.__setattr__(bad, "result", "legacy_v0")
    object.__setattr__(bad, "highlights", "x")
    object.__setattr__(bad, "user_caption", None)

    with pytest.raises(ShareCardRendererError, match="unknown result"):
        renderer.render_session_card(bad)


def test_qrcode_changes_pixels_in_bottom_right_corner() -> None:
    """QR sits in the bottom-right ~200×200 region. Without QR that
    region is pure gradient (each row is one solid color); with QR we
    inject white pixels that break that uniformity."""
    renderer = PillowShareCardRenderer()
    without_qr = Image.open(BytesIO(renderer.render_session_card(_sample_data())))
    with_qr = Image.open(BytesIO(renderer.render_session_card(_sample_data(), include_qrcode=True)))

    box = (CARD_WIDTH - 240, CARD_HEIGHT - 260, CARD_WIDTH - 60, CARD_HEIGHT - 80)
    assert without_qr.crop(box).tobytes() != with_qr.crop(box).tobytes()


def test_render_caches_fonts_across_calls() -> None:
    """Same renderer, two renders → font cache stays warm (no re-truetype)."""
    renderer = PillowShareCardRenderer()
    renderer.render_session_card(_sample_data())
    cached_sizes = set(renderer._font_cache.keys())
    renderer.render_session_card(_sample_data(result="fanche"))
    assert set(renderer._font_cache.keys()) == cached_sizes


def test_explicit_missing_font_path_raises() -> None:
    with pytest.raises(ShareCardRendererError, match="font_path does not exist"):
        PillowShareCardRenderer(font_path=Path("/nonexistent/font.ttf"))


def test_user_caption_alters_rendered_bytes() -> None:
    """Without a caption the 1100-px region is plain gradient; with it,
    text glyphs change the bytes. Doesn't pin font output, just asserts
    that the override actually reaches the canvas."""
    renderer = PillowShareCardRenderer()
    plain = renderer.render_session_card(_sample_data())
    captioned = renderer.render_session_card(_sample_data(user_caption="今天嘴硬了一把"))
    assert plain != captioned


# ---------------------------------------------------------------------
# Weekly + wrapped renderer tests
# ---------------------------------------------------------------------


def _weekly_data(**overrides: object) -> WeeklyCardData:
    defaults: dict[str, object] = {
        "week_label": "2026 第 20 周",
        "sessions_count": 3,
        "shenfeng_count": 1,
        "guolu_count": 1,
        "fanche_count": 1,
        "top_scenario_title": "周末加班谈判",
        "headline": "稳步向前",
    }
    defaults.update(overrides)
    return WeeklyCardData(**defaults)  # type: ignore[arg-type]


def _wrapped_data(**overrides: object) -> WrappedCardData:
    defaults: dict[str, object] = {
        "year": 2026,
        "total_sessions": 42,
        "top_scenario_title": "周末加班谈判",
        "top_opponent_title": "强硬型 HR",
        "best_session_title": "实习转正薪资谈判",
        "worst_session_title": "导师让无偿干私活",
        "closing_letter": "2026 你练了 42 把，K 都看在眼里",
    }
    defaults.update(overrides)
    return WrappedCardData(**defaults)  # type: ignore[arg-type]


def test_weekly_card_renders_to_expected_size_png() -> None:
    renderer = PillowShareCardRenderer()
    png = renderer.render_weekly_card(_weekly_data())

    assert png.startswith(b"\x89PNG\r\n\x1a\n")
    with Image.open(BytesIO(png)) as im:
        assert im.size == (CARD_WIDTH, CARD_HEIGHT)


def test_weekly_card_empty_week_still_produces_valid_png() -> None:
    renderer = PillowShareCardRenderer()
    empty = _weekly_data(
        sessions_count=0,
        shenfeng_count=0,
        guolu_count=0,
        fanche_count=0,
        top_scenario_title=None,
        headline="本周 K 在等你",
    )
    png = renderer.render_weekly_card(empty)
    assert png.startswith(b"\x89PNG\r\n\x1a\n")


def test_wrapped_pages_returns_exactly_six_valid_pngs() -> None:
    renderer = PillowShareCardRenderer()
    pages = renderer.render_wrapped_pages(_wrapped_data())

    assert len(pages) == 6
    for page in pages:
        assert page.startswith(b"\x89PNG\r\n\x1a\n")
        with Image.open(BytesIO(page)) as im:
            assert im.size == (CARD_WIDTH, CARD_HEIGHT)


def test_wrapped_pages_empty_year_still_produces_six_pages() -> None:
    """An empty-year wrapped emits the same shape so the service can
    render every page slot unconditionally."""
    renderer = PillowShareCardRenderer()
    pages = renderer.render_wrapped_pages(
        _wrapped_data(
            total_sessions=0,
            top_scenario_title="—",
            top_opponent_title="—",
            best_session_title="—",
            worst_session_title="—",
            closing_letter="明年再来，K 等你",
        )
    )
    assert len(pages) == 6
    for page in pages:
        assert page.startswith(b"\x89PNG\r\n\x1a\n")
