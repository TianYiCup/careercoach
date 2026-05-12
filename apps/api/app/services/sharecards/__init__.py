"""Wrapped 卡分享渲染层 — PR ② of D6.

Public surface:
  * `ShareCardRenderer` Protocol + `ShareCardRendererError`
  * `SessionCardData` — the renderer's input payload
  * `PillowShareCardRenderer` — v0 reference implementation

PR ③ adds the `ShareCardService` orchestrator + storage + DB model
that turn `bytes` into a stored `ShareCardResponse`.
"""

from app.services.sharecards.pillow_renderer import PillowShareCardRenderer
from app.services.sharecards.renderer import (
    CARD_HEIGHT,
    CARD_WIDTH,
    ShareCardRenderer,
    ShareCardRendererError,
)
from app.services.sharecards.types import SessionCardData

__all__ = [
    "CARD_HEIGHT",
    "CARD_WIDTH",
    "PillowShareCardRenderer",
    "SessionCardData",
    "ShareCardRenderer",
    "ShareCardRendererError",
]
