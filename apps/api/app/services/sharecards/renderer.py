"""Renderer Protocol — concrete impls (Pillow today, Skia later) live next door.

We render to raw PNG bytes; the service layer (PR ③) hands those bytes
to whichever storage backend is configured. Rendering is sync because
it's CPU-bound — the service wraps the call in `asyncio.to_thread` so
the FastAPI event loop stays free.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from app.services.sharecards.types import SessionCardData

CARD_WIDTH = 1080
CARD_HEIGHT = 1920
"""9:16 share-card dimensions (design-spec §10.1). Hard-coded — all
templates must produce exactly this size so frontend layout stays
predictable and OG share-preview crops behave."""


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
    """Renders a `SessionCardData` into a 1080×1920 PNG byte string."""

    name: str

    def render_session_card(
        self,
        data: SessionCardData,
        *,
        include_qrcode: bool = False,
    ) -> bytes:
        """Return PNG bytes. Must raise `ShareCardRendererError` on failure, never None."""
        ...
