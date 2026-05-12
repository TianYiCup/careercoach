"""`ShareCardService` — orchestrates one share-card request.

End-to-end flow for `POST /v1/sharecards/session/{session_id}`:

    request → service.create_session_card(...)
            → score_repo.get(session_id)                 # 404 if missing
            → moderation.check(user_caption_override)    # only if caption present
            → asyncio.to_thread(renderer.render_session_card)
            → storage.put(card_id, png_bytes)            # writes PNG
            → INSERT INTO sharecards                     # audit row
            → ShareCardResponse

PR ③ wires the `session` card path; `weekly` and `wrapped` stay 501
until their renderers ship.
"""

from __future__ import annotations

import asyncio
import uuid
from dataclasses import replace
from datetime import UTC, datetime

import structlog
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.models.sharecard import ShareCard
from app.schemas.moderation import ModerationCheckRequest
from app.schemas.sharecards import (
    SessionShareCardRequest,
    ShareCardResponse,
    ShareLinks,
)
from app.services.moderation.service import ModerationService
from app.services.sharecards.renderer import ShareCardRenderer
from app.services.sharecards.session_score import SessionScoreRepository
from app.services.sharecards.storage import ShareCardStorage

logger = structlog.get_logger(__name__)


class ShareCardNotFoundError(LookupError):
    """Session has no scorecard yet — route maps to 404."""


class ShareCardCaptionBlockedError(RuntimeError):
    """User caption tripped moderation — route maps to 400 (PRD §7.8)."""

    def __init__(self, *, categories: tuple[str, ...]) -> None:
        super().__init__(f"caption blocked by moderation: {categories}")
        self.categories = categories


class ShareCardService:
    """Owns the four concerns no individual component should know about:
    public schema, score lookup, moderation, renderer + storage + audit."""

    def __init__(
        self,
        *,
        renderer: ShareCardRenderer,
        storage: ShareCardStorage,
        score_repo: SessionScoreRepository,
        moderation: ModerationService,
        async_session_factory: async_sessionmaker[AsyncSession] | None,
        app_share_origin: str = "https://careercoach.app",
    ) -> None:
        self._renderer = renderer
        self._storage = storage
        self._score_repo = score_repo
        self._moderation = moderation
        self._async_session_factory = async_session_factory
        self._app_share_origin = app_share_origin.rstrip("/")

    async def create_session_card(
        self,
        *,
        session_id: str,
        request: SessionShareCardRequest,
        user_id: str,
        trace_id: str,
    ) -> ShareCardResponse:
        score = await self._score_repo.get(session_id, user_id=user_id)
        if score is None:
            raise ShareCardNotFoundError(session_id)

        if request.user_caption_override:
            await self._gate_caption(
                request.user_caption_override,
                user_id=user_id,
                session_id=session_id,
                trace_id=trace_id,
            )
            score = replace(score, user_caption=request.user_caption_override)

        card_id = _new_card_id()
        png_bytes = await asyncio.to_thread(
            self._renderer.render_session_card,
            score,
            include_qrcode=request.include_qrcode,
        )

        png_url = await self._storage.put(card_id, png_bytes)
        await self._persist_row(
            card_id=card_id,
            card_type="session",
            user_id=user_id,
            session_id=session_id,
            user_caption=request.user_caption_override,
        )

        return ShareCardResponse(
            card_id=card_id,
            type="session",
            png_url=png_url,
            pages=[],
            share_links=self._build_share_links(card_id, png_url),
            generated_at=datetime.now(UTC),
        )

    async def _gate_caption(
        self,
        caption: str,
        *,
        user_id: str,
        session_id: str,
        trace_id: str,
    ) -> None:
        decision = await self._moderation.check(
            ModerationCheckRequest(
                content=caption,
                context="user_input",
                user_id=user_id,
                session_id=session_id,
            ),
            trace_id=trace_id,
        )
        if decision.verdict != "allow":
            logger.info(
                "sharecard_caption_blocked",
                trace_id=trace_id,
                user_id=user_id,
                verdict=decision.verdict,
                categories=list(decision.categories),
            )
            raise ShareCardCaptionBlockedError(categories=tuple(decision.categories))

    def _build_share_links(self, card_id: str, png_url: str) -> ShareLinks:
        return ShareLinks(
            wechat=f"weixin://dl/share?card={card_id}",
            xiaohongshu=f"{self._app_share_origin}/share/{card_id}?ch=xhs",
            save_local=png_url,
        )

    async def _persist_row(
        self,
        *,
        card_id: str,
        card_type: str,
        user_id: str,
        session_id: str | None,
        user_caption: str | None,
    ) -> None:
        # The session factory is `None` in unit tests that aren't wired
        # to a DB; we still want the rendering pipeline to work for
        # those, so audit writes degrade to a structured log.
        if self._async_session_factory is None:
            logger.debug(
                "sharecard_row_skipped",
                card_id=card_id,
                reason="no async_session_factory configured",
            )
            return
        try:
            async with self._async_session_factory() as session:
                session.add(
                    ShareCard(
                        card_id=card_id,
                        card_type=card_type,
                        user_id=user_id,
                        session_id=session_id,
                        storage_key=card_id,
                        storage_backend=self._storage.name,
                        user_caption=user_caption,
                    )
                )
                await session.commit()
        except Exception:
            # An audit failure must NOT block the user response — same
            # policy moderation uses (PRD §6.2 prefers visibility over
            # availability tradeoffs here).
            logger.exception(
                "sharecard_audit_failed",
                card_id=card_id,
                card_type=card_type,
                user_id=user_id,
            )


def _new_card_id() -> str:
    """`card_<16-hex>` — collision-safe enough for v0 (2^64 keyspace)."""
    return f"card_{uuid.uuid4().hex[:16]}"
