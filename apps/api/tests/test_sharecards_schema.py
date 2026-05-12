"""Pydantic schema gates for the Wrapped 卡 system (PR ① of D6).

The routes still raise 501; what we lock here is the request shape so
the frontend can codegen against `openapi.yaml` and MSW can stage
realistic fixtures.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from app.schemas.sharecards import (
    SessionShareCardRequest,
    ShareCardResponse,
    ShareLinks,
    WeeklyShareCardRequest,
    WrappedShareCardRequest,
)
from pydantic import ValidationError


def test_session_request_defaults_disable_qrcode_and_caption() -> None:
    req = SessionShareCardRequest()
    assert req.include_qrcode is False
    assert req.user_caption_override is None


def test_session_request_caps_user_caption_at_80_chars() -> None:
    SessionShareCardRequest(user_caption_override="x" * 80)
    with pytest.raises(ValidationError):
        SessionShareCardRequest(user_caption_override="x" * 81)


def test_weekly_request_clamps_week_offset_to_last_12_weeks() -> None:
    WeeklyShareCardRequest(week_offset=0)
    WeeklyShareCardRequest(week_offset=-12)

    with pytest.raises(ValidationError):
        WeeklyShareCardRequest(week_offset=1)
    with pytest.raises(ValidationError):
        WeeklyShareCardRequest(week_offset=-13)


def test_wrapped_request_defaults_qrcode_on_for_virality() -> None:
    req = WrappedShareCardRequest()
    assert req.include_qrcode is True


def test_response_envelope_round_trips() -> None:
    payload = {
        "card_id": "card_demo",
        "type": "session",
        "png_url": "https://cdn.example.com/card_demo.png",
        "pages": [],
        "share_links": {
            "wechat": "weixin://dl/share?card=card_demo",
            "xiaohongshu": "https://www.xiaohongshu.com/share?img=card_demo",
            "save_local": "https://cdn.example.com/card_demo.png",
        },
        "generated_at": "2026-05-12T13:45:00Z",
    }
    parsed = ShareCardResponse.model_validate(payload)
    assert parsed.type == "session"
    assert parsed.share_links.wechat.startswith("weixin://")
    assert parsed.generated_at == datetime(2026, 5, 12, 13, 45, tzinfo=UTC)


def test_response_rejects_unknown_card_type() -> None:
    with pytest.raises(ValidationError):
        ShareCardResponse(
            card_id="card_x",
            type="legacy_2019",
            png_url="https://cdn.example.com/x.png",
            share_links=ShareLinks(
                wechat="weixin://x",
                xiaohongshu="https://www.xiaohongshu.com/x",
                save_local="https://cdn.example.com/x.png",
            ),
            generated_at=datetime.now(UTC),
        )
