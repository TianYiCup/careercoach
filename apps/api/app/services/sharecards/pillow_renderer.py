"""Pillow-based session-card renderer (PR ② of D6).

Implements `ShareCardRenderer.render_session_card` for the v0 session
template (design-spec §10.1):

    ┌─────────────────┐
    │   gradient bg    │   ← gradient varies by result (glory/vivid/crash)
    │                  │
    │    [K 圆环]       │   ← purple placeholder; real Rive K lands later
    │                  │
    │     8.9          │   ← overall score (italic-ish via large font)
    │   今日封神        │   ← verdict from result
    │                  │
    │   "user caption"  │   ← optional override
    │                  │
    │  气场 +9  逻辑 +8  │   ← top-3 dimension badges
    │  达成 +7          │
    │                  │
    │  CareerCoach AI   │   ← watermark
    │   教练 K 出品      │
    │              [QR] │   ← optional bottom-right QR
    └─────────────────┘

Fonts
-----
We try to locate a CJK system font (YaHei on Windows, PingFang on
macOS, Noto Sans CJK on Linux). If none is found we fall back to
Pillow's default bitmap font and log a warning — Latin still renders,
CJK becomes tofu boxes. v1 will bundle a subsetted Noto Sans SC so
deployments don't depend on host fonts.
"""

from __future__ import annotations

import platform
from io import BytesIO
from pathlib import Path

import qrcode
import structlog
from PIL import Image, ImageDraw, ImageFont
from qrcode.image.pil import PilImage

from app.services.sharecards.renderer import (
    CARD_HEIGHT,
    CARD_WIDTH,
    ShareCardRendererError,
)
from app.services.sharecards.types import SessionCardData

logger = structlog.get_logger(__name__)


_RGB = tuple[int, int, int]

# Design-spec §2.3 gradient palettes (approx — converted from the
# 135deg CSS linear-gradient stops to RGB top/bottom for vertical fill).
_GRADIENTS: dict[str, tuple[_RGB, _RGB]] = {
    "shenfeng": ((176, 255, 60), (60, 255, 232)),  # gradient-glory  · 封神
    "guolu": ((255, 122, 224), (94, 162, 255)),  # gradient-vivid  · 路过
    "fanche": ((255, 107, 53), (255, 122, 182)),  # gradient-crash  · 翻车
}

_VERDICTS: dict[str, str] = {
    "shenfeng": "今日封神",
    "guolu": "稳如老狗",
    "fanche": "翻车现场",
}

_PLACEHOLDER_K_COLOR: _RGB = (167, 139, 250)  # #A78BFA · brand purple

# Common CJK system font paths, searched in order per OS.
_FONT_CANDIDATES_BY_OS: dict[str, tuple[str, ...]] = {
    "Windows": (
        "C:/Windows/Fonts/msyh.ttc",
        "C:/Windows/Fonts/msyhbd.ttc",
        "C:/Windows/Fonts/simhei.ttf",
    ),
    "Darwin": (
        "/System/Library/Fonts/PingFang.ttc",
        "/System/Library/Fonts/STHeiti Medium.ttc",
    ),
    "Linux": (
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/truetype/noto/NotoSansSC-Regular.otf",
        "/usr/share/fonts/truetype/wqy/wqy-microhei.ttc",
    ),
}


class PillowShareCardRenderer:
    """v0 session-card renderer. Structurally implements `ShareCardRenderer`."""

    name: str = "pillow"

    def __init__(
        self,
        *,
        font_path: Path | None = None,
        app_share_origin: str = "https://careercoach.app",
    ) -> None:
        resolved = font_path or _detect_cjk_font()
        if resolved is not None and not resolved.exists():
            raise ShareCardRendererError(
                f"font_path does not exist: {resolved}", renderer=self.name
            )
        self._font_path = resolved
        self._app_share_origin = app_share_origin.rstrip("/")
        self._font_cache: dict[int, ImageFont.FreeTypeFont | ImageFont.ImageFont] = {}
        if self._font_path is None:
            logger.warning(
                "sharecard_renderer_no_cjk_font",
                msg="no CJK system font; using Pillow default — CJK will tofu",
            )

    def render_session_card(
        self,
        data: SessionCardData,
        *,
        include_qrcode: bool = False,
    ) -> bytes:
        try:
            top, bottom = _GRADIENTS[data.result]
        except KeyError as exc:
            raise ShareCardRendererError(
                f"unknown result: {data.result}", renderer=self.name
            ) from exc

        canvas = _make_vertical_gradient(CARD_WIDTH, CARD_HEIGHT, top, bottom)
        draw = ImageDraw.Draw(canvas)

        self._draw_k_placeholder(draw)
        self._draw_score_block(draw, data)
        if data.user_caption:
            self._draw_centered(
                draw, data.user_caption, y=1100, font=self._font(44), fill=(255, 255, 255)
            )
        self._draw_dimension_badges(draw, data)
        self._draw_watermark(draw)

        if include_qrcode:
            self._paste_qrcode(canvas, data)

        buf = BytesIO()
        canvas.save(buf, format="PNG", optimize=True)
        return buf.getvalue()

    def _draw_k_placeholder(self, draw: ImageDraw.ImageDraw) -> None:
        cx, cy, r = CARD_WIDTH // 2, 380, 160
        draw.ellipse((cx - r, cy - r, cx + r, cy + r), fill=_PLACEHOLDER_K_COLOR)
        self._draw_centered(draw, "K", y=cy - 110, font=self._font(180), fill=(255, 255, 255))

    def _draw_score_block(self, draw: ImageDraw.ImageDraw, data: SessionCardData) -> None:
        self._draw_centered(
            draw,
            f"{data.overall_score:.1f}",
            y=720,
            font=self._font(220),
            fill=(255, 255, 255),
        )
        self._draw_centered(
            draw,
            _VERDICTS[data.result],
            y=1000,
            font=self._font(84),
            fill=(255, 255, 255),
        )

    def _draw_dimension_badges(self, draw: ImageDraw.ImageDraw, data: SessionCardData) -> None:
        badge_font = self._font(40)
        for i, (label, score) in enumerate(data.top_dimensions):
            self._draw_centered(
                draw,
                f"{label}  +{score}",
                y=1300 + i * 70,
                font=badge_font,
                fill=(255, 255, 255),
            )

    def _draw_watermark(self, draw: ImageDraw.ImageDraw) -> None:
        self._draw_centered(
            draw,
            "CareerCoach AI · 教练 K 出品",
            y=1820,
            font=self._font(32),
            fill=(255, 255, 255),
        )

    def _draw_centered(
        self,
        draw: ImageDraw.ImageDraw,
        text: str,
        *,
        y: int,
        font: ImageFont.FreeTypeFont | ImageFont.ImageFont,
        fill: _RGB,
    ) -> None:
        bbox = draw.textbbox((0, 0), text, font=font)
        text_w = bbox[2] - bbox[0]
        x = (CARD_WIDTH - text_w) // 2
        draw.text((x, y), text, font=font, fill=fill)

    def _paste_qrcode(self, canvas: Image.Image, data: SessionCardData) -> None:
        # Real card_id arrives in PR ③ — for the renderer-only spike the
        # URL is keyed on scenario+persona so QRs are still unique.
        slug = f"{data.scenario_title}_{data.persona_title}".replace(" ", "_")
        url = f"{self._app_share_origin}/share/{slug}"

        qr = qrcode.QRCode(version=4, box_size=8, border=2)
        qr.add_data(url)
        qr.make(fit=True)
        # Pin the image factory so static analysis sees a concrete PilImage
        # instead of `PilImage | PyPNGImage`.
        qr_pil: PilImage = qr.make_image(
            image_factory=PilImage, fill_color="white", back_color=None
        )
        qr_img = qr_pil.convert("RGBA")

        x = CARD_WIDTH - qr_img.width - 60
        y = CARD_HEIGHT - qr_img.height - 80
        canvas.paste(qr_img, (x, y), qr_img)

    def _font(self, size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
        cached = self._font_cache.get(size)
        if cached is not None:
            return cached

        font: ImageFont.FreeTypeFont | ImageFont.ImageFont
        if self._font_path is not None:
            font = ImageFont.truetype(str(self._font_path), size)
        else:
            font = ImageFont.load_default(size)
        self._font_cache[size] = font
        return font


def _detect_cjk_font() -> Path | None:
    """Return the first existing CJK font path for the current OS, or None."""
    candidates = _FONT_CANDIDATES_BY_OS.get(platform.system(), ())
    for candidate in candidates:
        path = Path(candidate)
        if path.exists():
            return path
    return None


def _make_vertical_gradient(width: int, height: int, top: _RGB, bottom: _RGB) -> Image.Image:
    """Build a top→bottom RGB gradient by filling a 1-px strip then resizing.

    Per-pixel interpolation in pure Python is slow; the strip+resize
    trick stays well under 30 ms for 1080×1920 without pulling in numpy.
    """
    strip = Image.new("RGB", (1, height))
    for y in range(height):
        t = y / max(height - 1, 1)
        r = int(top[0] * (1 - t) + bottom[0] * t)
        g = int(top[1] * (1 - t) + bottom[1] * t)
        b = int(top[2] * (1 - t) + bottom[2] * t)
        strip.putpixel((0, y), (r, g, b))
    return strip.resize((width, height), Image.Resampling.NEAREST)
