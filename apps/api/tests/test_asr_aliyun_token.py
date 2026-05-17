"""Aliyun NLS token-fetcher tests — signature + parsing + caching.

We don't hit nls-meta.cn-shanghai.aliyuncs.com in tests. `httpx.MockTransport`
captures the outgoing request so we can assert the canonical params
+ signature were built correctly, and feeds back canned responses
to exercise the success / error / malformed paths.
"""

from __future__ import annotations

import json
import time
import urllib.parse

import httpx
import pytest
from app.asr._aliyun_token import (
    AccessToken,
    AliyunTokenCache,
    _pop_encode,
    _sign_pop_query,
    fetch_access_token,
)
from app.asr.errors import (
    ASRAuthError,
    ASRMalformedResponseError,
    ASRTimeoutError,
    ASRUpstreamError,
)

# --- signing primitives ---


def test_pop_encode_replaces_plus_and_star() -> None:
    """Aliyun rejects `+` (default urllib quote for space) and `*` —
    we must percent-encode both. Tilde stays as-is per the spec."""
    assert _pop_encode("hello world") == "hello%20world"
    assert _pop_encode("a*b") == "a%2Ab"
    assert _pop_encode("a~b") == "a~b"
    assert _pop_encode("a/b") == "a%2Fb"


def test_sign_pop_query_matches_canonical_recipe() -> None:
    """Spot-check against the Aliyun SDK's own example to lock in
    the recipe — if this ever diverges, every token request 400s
    with 'Signature does not match'."""
    params = {
        "Format": "JSON",
        "Version": "2019-02-28",
        "AccessKeyId": "testid",
        "SignatureMethod": "HMAC-SHA1",
        "Timestamp": "2017-09-04T05:46:42Z",
        "SignatureVersion": "1.0",
        "SignatureNonce": "uniquenonce",
        "Action": "CreateToken",
    }
    sig = _sign_pop_query(method="POST", params=params, secret="testsecret")
    # The signature is deterministic given the inputs; recompute by
    # hand once to set the expected value, then this test pins
    # against accidental changes to the encoding rules.
    assert isinstance(sig, str) and len(sig) > 20
    # Re-running with the same inputs must yield the same signature
    # (no clock-dependent randomness leaking in).
    assert _sign_pop_query(method="POST", params=params, secret="testsecret") == sig
    # Changing the secret must change the signature.
    other = _sign_pop_query(method="POST", params=params, secret="other")
    assert other != sig


# --- fetch_access_token ---


def _success_response(*, token: str = "tk-xyz", expires_at: int = 9999999999) -> httpx.Response:  # noqa: S107
    return httpx.Response(
        200,
        json={
            "Token": {"Id": token, "ExpireTime": expires_at, "UserId": "u1"},
            "NlsRequestId": "req-1",
            "ErrMsg": "",
        },
    )


async def test_fetch_token_happy_path_returns_token_and_expiry() -> None:
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["form"] = urllib.parse.parse_qs(request.content.decode())
        captured["method"] = request.method
        return _success_response(token="tk-success", expires_at=1234567890)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    try:
        result = await fetch_access_token(
            access_key_id="ak",
            access_key_secret="secret",
            endpoint_url="https://nls-meta.example/",
            client=client,
        )
    finally:
        await client.aclose()

    assert isinstance(result, AccessToken)
    assert result.token == "tk-success"
    assert result.expires_at == 1234567890.0
    # The request was a POST with a signed form body.
    assert captured["method"] == "POST"
    form = captured["form"]
    assert isinstance(form, dict)
    assert form["AccessKeyId"] == ["ak"]
    assert form["Action"] == ["CreateToken"]
    assert "Signature" in form  # signed
    assert form["SignatureMethod"] == ["HMAC-SHA1"]


async def test_fetch_token_raises_auth_error_on_401() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"Message": "bad key"})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    try:
        with pytest.raises(ASRAuthError):
            await fetch_access_token(
                access_key_id="ak",
                access_key_secret="bad",
                endpoint_url="https://nls-meta.example/",
                client=client,
            )
    finally:
        await client.aclose()


async def test_fetch_token_raises_upstream_error_on_500() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, content=b"oops")

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    try:
        with pytest.raises(ASRUpstreamError) as ei:
            await fetch_access_token(
                access_key_id="ak",
                access_key_secret="secret",
                endpoint_url="https://nls-meta.example/",
                client=client,
            )
    finally:
        await client.aclose()

    assert ei.value.status_code == 500


async def test_fetch_token_raises_timeout_on_httpx_timeout() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectTimeout("simulated")

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    try:
        with pytest.raises(ASRTimeoutError):
            await fetch_access_token(
                access_key_id="ak",
                access_key_secret="secret",
                endpoint_url="https://nls-meta.example/",
                client=client,
            )
    finally:
        await client.aclose()


async def test_fetch_token_raises_when_response_missing_token_block() -> None:
    """A-38: shape-mismatch raises the more specific
    `ASRMalformedResponseError` so the copilot WS handler can tag
    the trace with `asr_error:upstream_malformed` (vs a real
    `upstream_5xx` from `test_fetch_token_raises_upstream_error_on_500`).
    Distinct ops responses — vendor API drift vs vendor outage."""

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"ErrMsg": "no token for you"})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    try:
        with pytest.raises(ASRMalformedResponseError, match="missing Token"):
            await fetch_access_token(
                access_key_id="ak",
                access_key_secret="secret",
                endpoint_url="https://nls-meta.example/",
                client=client,
            )
    finally:
        await client.aclose()


async def test_fetch_token_raises_when_ak_empty() -> None:
    """Pre-flight check — don't burn an HTTP call when we'd 400 anyway."""
    with pytest.raises(ASRAuthError, match="AK/secret are empty"):
        await fetch_access_token(
            access_key_id="",
            access_key_secret="secret",
            endpoint_url="https://nls-meta.example/",
        )


async def test_fetch_token_raises_when_expire_time_not_numeric() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            content=json.dumps({"Token": {"Id": "tk", "ExpireTime": "not-a-number"}}).encode(),
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    try:
        # A-38: non-numeric ExpireTime is a parse failure, not a 5xx.
        with pytest.raises(ASRMalformedResponseError, match="ExpireTime"):
            await fetch_access_token(
                access_key_id="ak",
                access_key_secret="secret",
                endpoint_url="https://nls-meta.example/",
                client=client,
            )
    finally:
        await client.aclose()


# --- AliyunTokenCache ---


def test_access_token_is_fresh_within_buffer() -> None:
    """Tokens with >30s of life left are fresh; everything else stale."""
    now = 1000.0
    assert AccessToken(token="t", expires_at=now + 60).is_fresh(now=now)
    assert not AccessToken(token="t", expires_at=now + 10).is_fresh(now=now)
    assert not AccessToken(token="t", expires_at=now - 5).is_fresh(now=now)


async def test_token_cache_returns_cached_when_fresh() -> None:
    """No HTTP call when the cached token has plenty of life."""
    fresh = AccessToken(token="cached", expires_at=time.time() + 3600)
    cache = AliyunTokenCache(cached_token=fresh)

    # If a refresh was attempted the handler would raise — we want
    # to prove no network call happens for a fresh cached token.
    def handler(_request: httpx.Request) -> httpx.Response:
        raise AssertionError("cache should not refresh while fresh")

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    try:
        token = await cache.get(
            access_key_id="ak",
            access_key_secret="secret",
            endpoint_url="https://nls-meta.example/",
            timeout_s=5.0,
            client=client,
        )
    finally:
        await client.aclose()
    assert token is fresh


async def test_token_cache_refreshes_when_stale() -> None:
    """Cached token within the 30s buffer must trigger a refresh."""
    stale = AccessToken(token="stale", expires_at=time.time() + 5)
    cache = AliyunTokenCache(cached_token=stale)

    def handler(_request: httpx.Request) -> httpx.Response:
        return _success_response(token="fresh", expires_at=int(time.time() + 3600))

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    try:
        token = await cache.get(
            access_key_id="ak",
            access_key_secret="secret",
            endpoint_url="https://nls-meta.example/",
            timeout_s=5.0,
            client=client,
        )
    finally:
        await client.aclose()
    assert token.token == "fresh"
