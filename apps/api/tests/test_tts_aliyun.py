"""Aliyun TTS adapter tests — skeleton-as-shipped.

The Aliyun TTS adapter ships as a backup-slot stub: it constructs with
the same credentials shape the ASR adapter expects, but its
`synthesize` raises `TTSUpstreamError` so the router treats it as
"configured but unavailable". When the real impl lands, these tests
become happy-path tests against a fake WS — the skeleton ones below
become regressions that pin the construction shape.
"""

from __future__ import annotations

import pytest
from app.tts import AliyunTTSProvider, TTSUpstreamError


def _make_provider() -> AliyunTTSProvider:
    return AliyunTTSProvider(
        access_key_id="ak",
        access_key_secret="secret",
        app_key="app",
        ws_url="wss://example.invalid/ws",
        token_url="https://nls-meta.example/",
    )


async def test_synthesize_raises_upstream_until_implemented() -> None:
    """Until the real wire protocol lands, the stub must raise a typed
    `TTSUpstreamError` so the router fails over to the next provider
    instead of silently returning nothing."""
    provider = _make_provider()

    with pytest.raises(TTSUpstreamError):
        async for _ in provider.synthesize("hi", voice="k-warm"):
            pass


async def test_provider_name_is_stable() -> None:
    """`name="aliyun"` is what Langfuse + logs filter on; pin it."""
    assert _make_provider().name == "aliyun"


async def test_construction_captures_secrets() -> None:
    """Constructor signature is the API a future real impl needs to
    preserve — keep this contract explicit so a refactor that changes
    the kwargs surfaces here, not at runtime."""
    provider = _make_provider()
    # `_access_key_id` etc. are name-mangled-by-convention attrs; we
    # touch them to fail the test if a rename happens silently.
    assert provider._access_key_id == "ak"
    assert provider._app_key == "app"
    assert provider._ws_url.startswith("wss://")
