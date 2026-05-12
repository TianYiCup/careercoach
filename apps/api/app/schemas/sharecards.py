"""Wrapped 卡分享系统 — PRD §7.9 / design-spec §10.

Three card types, one shape:
  * `session` — single sandbox scorecard (sandbox 评分后即时生成)
  * `weekly`  — weekly digest (server cron, pushed to device)
  * `wrapped` — annual 6-page Spotify-Wrapped style recap

v0.1 surface freezes:
  * request bodies (so the frontend can codegen)
  * the response envelope (`card_id`, `png_url`, `share_links`, `generated_at`)

PNG rendering, storage, and share-link signing all land in subsequent
PRs; the routes ship as 501 stubs in PR ①.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

ShareCardType = Literal["session", "weekly", "wrapped"]
"""The three templates we promise frontend. Drives gradient / layout / Mascot K pose."""


class ShareLinks(BaseModel):
    """One-tap targets we render under the saved card.

    `save_local` is the raw PNG URL the client uses to drop the image
    into the user's photo roll. `wechat` / `xiaohongshu` are deep-link
    URIs that pre-fill platform share sheets — they may carry a short
    UTM tag so analytics can attribute virality, but they MUST NOT
    leak PII (user_id, phone, etc.).
    """

    wechat: str = Field(
        ...,
        description="WeChat share deep link (weixin://...). Frontend opens via wx-sdk.",
        examples=["weixin://dl/share?card=sc_xxx"],
    )
    xiaohongshu: str = Field(
        ...,
        description="Xiaohongshu share intent URL.",
        examples=["https://www.xiaohongshu.com/share?img=..."],
    )
    save_local: str = Field(
        ...,
        description="Direct PNG URL for save-to-album. Same origin as `png_url`.",
        examples=["https://cdn.example.com/sharecards/sc_xxx.png"],
    )


class SessionShareCardRequest(BaseModel):
    """Body of POST /v1/sharecards/session/{session_id}.

    `session_id` itself rides in the URL path so frontend can use
    `useShareCardForSession(id)` patterns without juggling bodies.
    """

    include_qrcode: bool = Field(
        default=False,
        description=(
            "Render the bottom-right QR code that deep-links back to the app. "
            "Defaults to false — many users prefer screenshot-clean cards."
        ),
    )
    user_caption_override: str | None = Field(
        default=None,
        max_length=80,
        description=(
            "Optional user-edited one-liner replacing K's default verdict copy. "
            "Server still re-runs moderation (§7.8) before baking it into the PNG."
        ),
        examples=["今天嘴硬了一把"],
    )


class WeeklyShareCardRequest(BaseModel):
    """Body of POST /v1/sharecards/weekly. Manual trigger; cron also calls this."""

    include_qrcode: bool = Field(default=False)
    week_offset: int = Field(
        default=0,
        ge=-12,
        le=0,
        description=(
            "0 = last completed ISO week, -1 = week before that, etc. "
            "Bounded so users can't backfill the whole year via this endpoint."
        ),
        examples=[0],
    )


class WrappedShareCardRequest(BaseModel):
    """Body of POST /v1/sharecards/wrapped/year/{year}.

    The year lives in the URL — body carries cosmetic toggles only.
    """

    include_qrcode: bool = Field(
        default=True, description="Wrapped defaults to on — virality matters."
    )


class ShareCardResponse(BaseModel):
    """200 response shared by all three POSTs.

    A `wrapped` card carries 6 PNGs (one per page); we still ship a
    single `png_url` pointing at the cover, with the rest reachable
    via `pages[]`. The frontend treats `pages` as authoritative when
    `type == "wrapped"` and `png_url` as the OG cover for share
    previews.
    """

    card_id: str = Field(
        ...,
        description="Stable opaque id, also used as the storage object key.",
        examples=["sc_018f3a8b1c2d7e3a"],
    )
    type: ShareCardType
    png_url: str = Field(
        ...,
        description="1080×1920 PNG URL. CDN-signed; valid for at least 7 days.",
        examples=["https://cdn.example.com/sharecards/sc_xxx.png"],
    )
    pages: list[str] = Field(
        default_factory=list,
        description=(
            "Wrapped multi-page rendering. Empty for `session` and `weekly`; "
            "6 URLs in cover order for `wrapped`."
        ),
        examples=[[]],
    )
    share_links: ShareLinks
    generated_at: datetime = Field(
        ...,
        description="UTC timestamp at which the PNG was rendered.",
        examples=["2026-05-12T13:45:00Z"],
    )
