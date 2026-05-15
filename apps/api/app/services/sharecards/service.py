"""`ShareCardService` — orchestrates one share-card request.

End-to-end flow for `POST /v1/sharecards/session/{session_id}`:

    request → service.create_session_card(...)
            → score_repo.get(session_id)                 # 404 if missing
            → moderation.check(user_caption_override)    # only if caption present
            → asyncio.to_thread(renderer.render_session_card)
            → storage.put(card_id, png_bytes)            # writes PNG
            → INSERT INTO sharecards                     # audit row
            → ShareCardResponse

Weekly + wrapped follow the same shape with a different aggregator
and a different renderer entry point. Weekly emits one PNG, wrapped
emits six (cover + five inner pages) under
`<card_id>/p<index>_<slug>.png`.
"""

from __future__ import annotations

import asyncio
import uuid
from collections import Counter
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import structlog
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.models.sharecard import ShareCard
from app.schemas.moderation import ModerationCheckRequest
from app.schemas.sharecards import (
    SessionShareCardRequest,
    ShareCardResponse,
    ShareLinks,
    WeeklyShareCardRequest,
    WrappedShareCardRequest,
)
from app.services.moderation.service import ModerationService
from app.services.sharecards.renderer import (
    WRAPPED_PAGE_COUNT,
    ShareCardRenderer,
)
from app.services.sharecards.session_score import SessionScoreRepository
from app.services.sharecards.storage import ShareCardStorage
from app.services.sharecards.types import (
    SessionCardData,
    WeeklyCardData,
    WrappedCardData,
)

_SHANGHAI = ZoneInfo("Asia/Shanghai")
_WRAPPED_PAGE_SLUGS: tuple[str, ...] = (
    "p0_cover",
    "p1_scenario",
    "p2_opponent",
    "p3_best",
    "p4_worst",
    "p5_letter",
)

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
        is_minor: bool = False,
        trace_id: str,
    ) -> ShareCardResponse:
        score = await self._score_repo.get(session_id, user_id=user_id)
        if score is None:
            raise ShareCardNotFoundError(session_id)

        if request.user_caption_override:
            await self._gate_caption(
                request.user_caption_override,
                user_id=user_id,
                is_minor=is_minor,
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

    async def create_weekly_card(
        self,
        *,
        request: WeeklyShareCardRequest,
        user_id: str,
        trace_id: str,
        now: datetime | None = None,
    ) -> ShareCardResponse:
        """Build a weekly digest card from sessions in the requested
        Mon-Sun window (Asia/Shanghai).

        `now` is injectable so tests can pin "what week is it" without
        wall-clock flakiness. Production passes `None` and we use UTC.
        """
        anchor = now or datetime.now(UTC)
        since, until, week_label = _resolve_week_window(anchor, request.week_offset)

        sessions = await self._score_repo.list_for_user(user_id, since=since, until=until)
        data = _aggregate_weekly(week_label=week_label, sessions=sessions)

        card_id = _new_card_id()
        png_bytes = await asyncio.to_thread(
            self._renderer.render_weekly_card,
            data,
            include_qrcode=request.include_qrcode,
        )
        png_url = await self._storage.put(card_id, png_bytes)
        await self._persist_row(
            card_id=card_id,
            card_type="weekly",
            user_id=user_id,
            session_id=None,
            user_caption=None,
        )

        logger.info(
            "sharecard_weekly_created",
            card_id=card_id,
            user_id=user_id,
            trace_id=trace_id,
            week_label=week_label,
            sessions_count=data.sessions_count,
        )
        return ShareCardResponse(
            card_id=card_id,
            type="weekly",
            png_url=png_url,
            pages=[],
            share_links=self._build_share_links(card_id, png_url),
            generated_at=datetime.now(UTC),
        )

    async def create_wrapped_card(
        self,
        *,
        year: int,
        request: WrappedShareCardRequest,
        user_id: str,
        trace_id: str,
    ) -> ShareCardResponse:
        """Build an annual 6-page Wrapped recap from all sessions in the
        requested calendar year (Asia/Shanghai)."""
        since, until = _year_window(year)
        sessions = await self._score_repo.list_for_user(user_id, since=since, until=until)
        data = _aggregate_wrapped(year=year, sessions=sessions)

        card_id = _new_card_id()
        page_bytes = await asyncio.to_thread(
            self._renderer.render_wrapped_pages,
            data,
            include_qrcode=request.include_qrcode,
        )
        if len(page_bytes) != WRAPPED_PAGE_COUNT:
            # Renderer contract violation — surface loudly so we don't
            # silently ship a 5-page Wrapped.
            raise RuntimeError(
                f"renderer returned {len(page_bytes)} pages, expected {WRAPPED_PAGE_COUNT}"
            )

        page_urls: list[str] = []
        for index, slug in enumerate(_WRAPPED_PAGE_SLUGS):
            key = f"{card_id}/{slug}"
            url = await self._storage.put(key, page_bytes[index])
            page_urls.append(url)

        await self._persist_row(
            card_id=card_id,
            card_type="wrapped",
            user_id=user_id,
            session_id=None,
            user_caption=None,
        )

        logger.info(
            "sharecard_wrapped_created",
            card_id=card_id,
            user_id=user_id,
            trace_id=trace_id,
            year=year,
            total_sessions=data.total_sessions,
        )
        return ShareCardResponse(
            card_id=card_id,
            type="wrapped",
            # `png_url` equals `pages[0]` per the schema — frontend uses
            # this as the OG preview cover.
            png_url=page_urls[0],
            pages=page_urls,
            share_links=self._build_share_links(card_id, page_urls[0]),
            generated_at=datetime.now(UTC),
        )

    async def _gate_caption(
        self,
        caption: str,
        *,
        user_id: str,
        is_minor: bool,
        session_id: str,
        trace_id: str,
    ) -> None:
        decision = await self._moderation.check(
            ModerationCheckRequest(
                content=caption,
                context="user_input",
                session_id=session_id,
            ),
            user_id=user_id,
            is_minor=is_minor,
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


def _resolve_week_window(
    anchor: datetime,
    week_offset: int,
) -> tuple[datetime, datetime, str]:
    """Convert a `week_offset` to (since_utc, until_utc, week_label).

    `week_offset=0` is the immediately preceding ISO week — Mon 00:00:00
    through Sun 23:59:59 in Asia/Shanghai. Negative offsets walk
    further back. The window is returned in UTC because the score repo
    stamps in UTC.
    """
    shanghai_anchor = anchor.astimezone(_SHANGHAI)
    # Walk back to the start of the current ISO week, then offset to
    # the immediately preceding completed week.
    current_week_start = (shanghai_anchor - timedelta(days=shanghai_anchor.weekday())).replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    week_start_local = current_week_start + timedelta(weeks=week_offset - 1)
    week_end_local = week_start_local + timedelta(weeks=1)

    iso_year, iso_week, _ = week_start_local.isocalendar()
    label = f"{iso_year} 第 {iso_week} 周"

    return (
        week_start_local.astimezone(UTC),
        week_end_local.astimezone(UTC),
        label,
    )


def _year_window(year: int) -> tuple[datetime, datetime]:
    """[Jan 1 00:00, Jan 1 of next year 00:00) — both in UTC, anchored to
    Asia/Shanghai so the window aligns with the user's calendar year."""
    start_local = datetime(year, 1, 1, tzinfo=_SHANGHAI)
    end_local = datetime(year + 1, 1, 1, tzinfo=_SHANGHAI)
    return start_local.astimezone(UTC), end_local.astimezone(UTC)


def _aggregate_weekly(
    *,
    week_label: str,
    sessions: list[SessionCardData],
) -> WeeklyCardData:
    """Roll a week's worth of session scorecards into renderer input.

    The headline is templated by verdict mix so users see real signal
    even in early weeks — empty week says "本周 K 在等你", a glorious
    week says "封神周", and a mixed week says "稳步向前".
    """
    if not sessions:
        return WeeklyCardData(
            week_label=week_label,
            sessions_count=0,
            shenfeng_count=0,
            guolu_count=0,
            fanche_count=0,
            top_scenario_title=None,
            headline="本周 K 在等你",
        )
    verdicts = Counter(s.result for s in sessions)
    scenarios = Counter(s.scenario_title for s in sessions)
    shenfeng = verdicts.get("shenfeng", 0)
    fanche = verdicts.get("fanche", 0)
    if shenfeng >= max(verdicts.values()) and shenfeng > 0:
        headline = "封神周，稳"
    elif fanche >= max(verdicts.values()) and fanche > 0:
        headline = "翻车多，复盘走起"
    else:
        headline = "稳步向前"
    return WeeklyCardData(
        week_label=week_label,
        sessions_count=len(sessions),
        shenfeng_count=shenfeng,
        guolu_count=verdicts.get("guolu", 0),
        fanche_count=fanche,
        top_scenario_title=scenarios.most_common(1)[0][0],
        headline=headline,
    )


def _aggregate_wrapped(
    *,
    year: int,
    sessions: list[SessionCardData],
) -> WrappedCardData:
    """Roll a year's worth of session scorecards into the 6-page input.

    Empty year still produces a valid WrappedCardData (zero total,
    placeholder copy) so the route never 404s on a quiet user — the
    renderer flips to its empty-year layout."""
    if not sessions:
        return WrappedCardData(
            year=year,
            total_sessions=0,
            top_scenario_title="—",
            top_opponent_title="—",
            best_session_title="—",
            worst_session_title="—",
            closing_letter="明年再来，K 等你",
        )
    scenarios = Counter(s.scenario_title for s in sessions)
    opponents = Counter(s.persona_title for s in sessions)
    best = max(sessions, key=lambda s: s.overall_score)
    worst = min(sessions, key=lambda s: s.overall_score)
    return WrappedCardData(
        year=year,
        total_sessions=len(sessions),
        top_scenario_title=scenarios.most_common(1)[0][0],
        top_opponent_title=opponents.most_common(1)[0][0],
        best_session_title=best.scenario_title,
        worst_session_title=worst.scenario_title,
        closing_letter=f"{year} 你练了 {len(sessions)} 把，K 都看在眼里",
    )
