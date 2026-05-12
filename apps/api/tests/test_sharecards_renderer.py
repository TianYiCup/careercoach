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
