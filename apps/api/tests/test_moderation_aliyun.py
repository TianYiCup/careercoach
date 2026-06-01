"""Mocked-transport tests for `AliyunTextModerationBackend`.

These never touch the network — `httpx.MockTransport` answers every
request with whatever the test stages. A live API smoke is gated
behind `@pytest.mark.integration` so CI without AK keys skips it.

Responses use the Content-Moderation-2.0 (增强版) shape:
`Data.labels` (comma-separated) + `Data.reason.riskLevel`.
"""

from __future__ import annotations

import json
import os
from collections.abc import Callable

import httpx
import pytest
from app.config import get_settings
from app.services.moderation.aliyun import (
    ACTION,
    CLOUD_SCORE,
    CLOUD_WARN_SCORE,
    VERSION,
    AliyunTextModerationBackend,
)
from app.services.moderation.backend import ModerationBackendError
from app.services.moderation.local_dict import SELF_HARM_RESOURCE


def _backend_with(
    handler: Callable[[httpx.Request], httpx.Response],
    *,
    timeout_s: float = 0.8,
) -> AliyunTextModerationBackend:
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler), timeout=timeout_s)
    return AliyunTextModerationBackend(
        access_key_id="AK_TEST",
        access_key_secret="SK_TEST",
        endpoint="green-cip.cn-shanghai.aliyuncs.com",
        service="chat_detection",
        timeout_s=timeout_s,
        client=client,
    )


def _ok(labels: str = "", risk_level: str = "high") -> httpx.Response:
    """A 200 / Code 200 增强版 response with the given labels + riskLevel."""
    return httpx.Response(
        200,
        json={
            "RequestId": "req-1",
            "Code": 200,
            "Data": {
                "labels": labels,
                "reason": json.dumps({"riskLevel": risk_level}),
            },
        },
    )


async def test_signed_headers_carry_action_version_and_authorization() -> None:
    captured: dict[str, httpx.Request] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["req"] = request
        return _ok()

    backend = _backend_with(handler)
    await backend.evaluate("today's a good day", "user_input")
    await backend.aclose()

    req = captured["req"]
    assert req.headers["x-acs-action"] == ACTION
    assert req.headers["x-acs-version"] == VERSION
    assert req.headers["Authorization"].startswith("ACS3-HMAC-SHA256 Credential=AK_TEST")
    assert "Signature=" in req.headers["Authorization"]
    assert req.headers["Content-Type"] == "application/json"
    body = json.loads(req.content.decode("utf-8"))
    assert body["Service"] == "chat_detection"
    assert json.loads(body["ServiceParameters"]) == {"content": "today's a good day"}


async def test_empty_labels_returns_allow() -> None:
    backend = _backend_with(lambda _r: _ok(labels="", risk_level=""))
    decision = await backend.evaluate("hello", "user_input")
    await backend.aclose()

    assert decision.verdict == "allow"
    assert decision.score == 0.0
    assert decision.categories == ()


async def test_high_risk_label_blocks_with_mapped_category() -> None:
    backend = _backend_with(lambda _r: _ok("profanity", "high"))
    decision = await backend.evaluate("你他妈废物", "user_input")
    await backend.aclose()

    assert decision.verdict == "block"
    assert decision.categories == ("violence",)
    assert decision.score == CLOUD_SCORE


async def test_medium_risk_label_blocks() -> None:
    backend = _backend_with(lambda _r: _ok("political_content", "medium"))
    decision = await backend.evaluate("...", "user_input")
    await backend.aclose()

    assert decision.verdict == "block"
    assert decision.categories == ("political",)


async def test_low_risk_label_warns_not_blocks() -> None:
    """A `low` riskLevel hit (e.g. a generic promo `ad`) is surfaced as a
    soft `warn`, not a hard `block` — over-blocking benign promo would
    wreck the UX, and the local-dict floor still catches real red-lines."""
    backend = _backend_with(lambda _r: _ok("ad", "low"))
    decision = await backend.evaluate("加我微信有优惠", "user_input")
    await backend.aclose()

    assert decision.verdict == "warn"
    assert decision.categories == ("loan",)
    assert decision.score == CLOUD_WARN_SCORE


async def test_self_harm_label_redirects_with_shared_resource() -> None:
    """Both backends must surface the same hotline copy."""
    backend = _backend_with(lambda _r: _ok("self_harm", "high"))
    decision = await backend.evaluate("想死了算了", "user_input")
    await backend.aclose()

    assert decision.verdict == "redirect"
    assert decision.redirect_resource == SELF_HARM_RESOURCE
    assert "self_harm" in decision.categories


async def test_unknown_label_does_not_create_category() -> None:
    """Aliyun keeps adding labels; an unmapped one shouldn't crash or leak."""
    backend = _backend_with(lambda _r: _ok("label_not_in_our_map", "high"))
    decision = await backend.evaluate("hi", "user_input")
    await backend.aclose()

    assert decision.verdict == "allow"
    assert decision.categories == ()


async def test_multiple_labels_map_to_distinct_categories() -> None:
    backend = _backend_with(lambda _r: _ok("sexual_content,political_content", "high"))
    decision = await backend.evaluate("mixed", "user_input")
    await backend.aclose()

    assert decision.verdict == "block"
    assert set(decision.categories) == {"harassment", "political"}


async def test_self_harm_wins_over_other_categories() -> None:
    backend = _backend_with(lambda _r: _ok("profanity,self_harm", "high"))
    decision = await backend.evaluate("mixed", "user_input")
    await backend.aclose()

    assert decision.verdict == "redirect"
    assert decision.redirect_resource == SELF_HARM_RESOURCE
    assert "self_harm" in decision.categories
    assert "violence" in decision.categories


async def test_malformed_reason_falls_back_to_soft_warn() -> None:
    """A mapped label with an unparseable `reason` still surfaces (as a
    soft warn) rather than being dropped to allow."""

    def handler(_r: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"Code": 200, "Data": {"labels": "profanity", "reason": "not-json"}},
        )

    backend = _backend_with(handler)
    decision = await backend.evaluate("borderline", "user_input")
    await backend.aclose()

    assert decision.verdict == "warn"
    assert decision.categories == ("violence",)


async def test_http_5xx_raises_backend_error() -> None:
    backend = _backend_with(lambda _r: httpx.Response(500, text="upstream blew up"))

    with pytest.raises(ModerationBackendError, match="HTTP 500"):
        await backend.evaluate("hi", "user_input")
    await backend.aclose()


async def test_business_code_non_200_raises_backend_error() -> None:
    backend = _backend_with(
        lambda _r: httpx.Response(
            200,
            json={"RequestId": "r", "Code": 400, "Message": "InvalidParameter"},
        )
    )

    with pytest.raises(ModerationBackendError, match="non-200 Code: 400"):
        await backend.evaluate("hi", "user_input")
    await backend.aclose()


async def test_timeout_raises_backend_error() -> None:
    def handler(_req: httpx.Request) -> httpx.Response:
        raise httpx.TimeoutException("simulated timeout")

    backend = _backend_with(handler)

    with pytest.raises(ModerationBackendError, match="timed out"):
        await backend.evaluate("hi", "user_input")
    await backend.aclose()


async def test_transport_error_raises_backend_error() -> None:
    def handler(_req: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("dns blew up")

    backend = _backend_with(handler)

    with pytest.raises(ModerationBackendError, match="transport error"):
        await backend.evaluate("hi", "user_input")
    await backend.aclose()


def test_init_rejects_empty_credentials() -> None:
    with pytest.raises(ValueError, match="non-empty credentials"):
        AliyunTextModerationBackend(
            access_key_id="",
            access_key_secret="SK",
            endpoint="green-cip.cn-shanghai.aliyuncs.com",
            service="chat_detection",
            timeout_s=0.8,
        )


@pytest.mark.integration
async def test_aliyun_live_call_returns_decision() -> None:
    """Live smoke against Aliyun — skipped when AK isn't configured.

    Run locally with:
        ALIYUN_ACCESS_KEY_ID=xxx ALIYUN_ACCESS_KEY_SECRET=yyy \\
        uv run pytest -m integration tests/test_moderation_aliyun.py
    """
    settings = get_settings()
    if not (
        settings.aliyun_access_key_id.get_secret_value()
        and settings.aliyun_access_key_secret.get_secret_value()
    ):
        pytest.skip("aliyun credentials not configured")
    if os.environ.get("CI"):  # don't burn quota from CI by default
        pytest.skip("integration test skipped in CI")

    backend = AliyunTextModerationBackend.from_settings(settings)
    try:
        decision = await backend.evaluate("今天天气真好", "user_input")
    finally:
        await backend.aclose()

    assert decision.verdict in {"allow", "warn", "block", "redirect"}
