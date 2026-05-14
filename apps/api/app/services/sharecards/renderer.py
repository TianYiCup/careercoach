"""Renderer Protocol — concrete impls (Pillow today, Skia later) live next door.

We render to raw PNG bytes; the service layer hands those bytes to
whichever storage backend is configured. Rendering is sync because
it's CPU-bound — the service wraps the calls in `asyncio.to_thread` so
the FastAPI event loop stays free.

Three card types, three render methods. Each takes its own data class
so the renderer can't accidentally cross-render (a `WeeklyCardData`
into a session layout, etc.); the type checker keeps them apart.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from app.services.sharecards.types import (
    SessionCardData,
    WeeklyCardData,
    WrappedCardData,
)

CARD_WIDTH = 1080
CARD_HEIGHT = 1920
"""9:16 share-card dimensions (design-spec §10.1). Hard-coded — all
templates must produce exactly this size so frontend layout stays
predictable and OG share-preview crops behave."""

WRAPPED_PAGE_COUNT = 6
"""Wrapped fans out into six fixed pages (design-spec §10.3). The
renderer returns exactly this many PNGs; the service writes each
under the storage key `<card_id>/p<index>_<slug>.png`."""


class ShareCardRendererError(RuntimeError):
    """Renderer failure surfaced to the service layer.

    The service handles mapping this to the moderation-style envelope
    in the route response; the renderer itself just describes what blew
    up + which implementation produced the error.
    """

    def __init__(self, message: str, *, renderer: str) -> None:
        super().__init__(message)
        self.renderer = renderer


@runtime_checkable
class ShareCardRenderer(Protocol):
    """Renders the three card variants into 1080×1920 PNG bytes."""

    name: str

    def render_session_card(
        self,
        data: SessionCardData,
        *,
        include_qrcode: bool = False,
    ) -> bytes:
        """Return PNG bytes. Must raise `ShareCardRendererError` on failure, never None."""
        ...

    def render_weekly_card(
        self,
        data: WeeklyCardData,
        *,
        include_qrcode: bool = False,
    ) -> bytes:
        """Single PNG; layout differs from session card. Same failure contract."""
        ...

    def render_wrapped_pages(
        self,
        data: WrappedCardData,
        *,
        include_qrcode: bool = True,
    ) -> list[bytes]:
        """Return exactly `WRAPPED_PAGE_COUNT` PNGs in display order
        (cover first). Same failure contract."""
        ...
