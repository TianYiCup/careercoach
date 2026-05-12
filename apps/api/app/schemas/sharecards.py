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

Naming note
-----------
We use `card_` as the share-card id prefix to avoid colliding with
the `sc_` prefix already reserved for scenarios (`sc_001`). PRD §7.9
example copy uses `sc_xxx` — that's been raised for PRD nit-fix; the
schema here is the source of truth for v0.1 codegen.
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
        examples=["weixin://dl/share?card=card_018f3a8b1c2d7e3a"],
    )
    xiaohongshu: str = Field(
        ...,
        description="Xiaohongshu share intent URL.",
        examples=["https://www.xiaohongshu.com/share?img=card_018f3a8b1c2d7e3a"],
    )
    save_local: str = Field(
        ...,
        description="Direct PNG URL for save-to-album. Same origin as `png_url`.",
        examples=["https://cdn.example.com/sharecards/card_018f3a8b1c2d7e3a.png"],
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

    include_qrcode: bool = Field(
        default=False,
        description=(
            "Weekly is opt-in viral — most users glance and dismiss; "
            "Wrapped defaults the QR ON because that card is built to share. "
            "Flip this to true to surface the QR for users who do want to share."
        ),
    )
    week_offset: int = Field(
        default=0,
        ge=-12,
        le=0,
        description=(
            "0 = the immediately preceding ISO calendar week (Mon-Sun in "
            "`Asia/Shanghai`). -1 = the week before that, and so on, down to -12. "
            "Bounded so users can't backfill the whole year via this endpoint."
        ),
        examples=[0],
    )


class WrappedShareCardRequest(BaseModel):
    """Body of POST /v1/sharecards/wrapped/year/{year}.

    The year lives in the URL — body carries cosmetic toggles only.
    Empty-by-design today; if the field set grows post-v0.1 we'll keep
    backward compatibility by adding only optional fields.
    """

    include_qrcode: bool = Field(
        default=True, description="Wrapped defaults to on — virality matters."
    )


class ShareCardResponse(BaseModel):
    """200 response shared by all three POSTs.

    A `wrapped` card carries 6 PNGs (one per page); `png_url` always
    points at `pages[0]` (the cover, which doubles as the OG share
    preview). For `session` / `weekly` the card is a single image and
    `pages` is empty.
    """

    card_id: str = Field(
        ...,
        description=(
            "Stable opaque id, also used as the storage object key. "
            "Prefix `card_` — distinct from `sc_` scenario ids and `ses_` "
            "session ids so log grep is unambiguous."
        ),
        examples=["card_018f3a8b1c2d7e3a"],
    )
    type: ShareCardType
    png_url: str = Field(
        ...,
        description=(
            "1080×1920 PNG URL. Presigned — frontend should re-request the "
            "card if the URL has expired. For `wrapped` this URL equals `pages[0]` "
            "and is the cover used by share-preview cards (OG / WeChat)."
        ),
        examples=["https://cdn.example.com/sharecards/card_018f3a8b1c2d7e3a.png"],
    )
    pages: list[str] = Field(
        default_factory=list,
        description=(
            "All pages in display order. Empty for `session` and `weekly`; "
            "for `wrapped` contains 6 URLs where `pages[0]` IS the cover and "
            "equals `png_url` — frontend should render `pages` only and not "
            "show the cover twice."
        ),
        examples=[
            [
                "https://cdn.example.com/sharecards/card_xxx/p0_cover.png",
                "https://cdn.example.com/sharecards/card_xxx/p1_count.png",
                "https://cdn.example.com/sharecards/card_xxx/p2_opponent.png",
                "https://cdn.example.com/sharecards/card_xxx/p3_shenfeng.png",
                "https://cdn.example.com/sharecards/card_xxx/p4_fanche.png",
                "https://cdn.example.com/sharecards/card_xxx/p5_letter.png",
            ]
        ],
    )
    share_links: ShareLinks
    generated_at: datetime = Field(
        ...,
        description="UTC timestamp at which the PNG was rendered.",
        examples=["2026-05-12T13:45:00Z"],
    )
