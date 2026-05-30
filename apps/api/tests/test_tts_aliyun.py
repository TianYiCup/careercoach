"""Aliyun TTS adapter tests — real NLS streaming implementation.

Drives the adapter against an in-memory fake WebSocket so the wire
protocol (StartSynthesis → binary audio frames → SynthesisCompleted) is
exercised without a live Aliyun account. The construction / name tests
pin the kwargs surface the factory depends on.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator

import pytest
from app.asr._aliyun_token import AccessToken, AliyunTokenCache
from app.tts import AliyunTTSProvider, TTSUpstreamError
from websockets.exceptions import ConnectionClosed


class _FakeWSChannel:
    """In-memory channel mirroring the bits of the real
    `WebSocketClientProtocol` we touch."""

    def __init__(self, to_send: list[str | bytes]) -> None:
        self.sent: list[str | bytes] = []
        self._to_send = list(to_send)
        self.close_calls: list[tuple[int, str]] = []

    async def send(self, message: bytes | str) -> None:
        self.sent.append(message)

    async def recv(self) -> bytes | str:
        if not self._to_send:
            raise ConnectionClosed(rcvd=None, sent=None)
        return self._to_send.pop(0)

    async def close(self, code: int = 1000, reason: str = "") -> None:
        self.close_calls.append((code, reason))


class _FakeWSConnection:
    def __init__(self, channel: _FakeWSChannel) -> None:
        self.channel = channel

    async def __aenter__(self) -> _FakeWSChannel:
        return self.channel

    async def __aexit__(self, *exc: object) -> None:
        return None


def _ws_factory(channel: _FakeWSChannel) -> object:
    state: dict[str, object] = {"last_url": None}

    async def factory(url: str) -> _FakeWSConnection:
        state["last_url"] = url
        return _FakeWSConnection(channel)

    factory.state = state  # type: ignore[attr-defined]
    return factory


def _synthesis_completed() -> str:
    return json.dumps({"header": {"name": "SynthesisCompleted", "status": 20000000}})


def _task_failed(message: str) -> str:
    return json.dumps(
        {"header": {"name": "TaskFailed", "status_text": message, "status": 40000004}}
    )


def _make_provider(channel: _FakeWSChannel) -> AliyunTTSProvider:
    cache = AliyunTokenCache(cached_token=AccessToken(token="tk", expires_at=9999999999))
    return AliyunTTSProvider(
        access_key_id="ak",
        access_key_secret="secret",
        app_key="app",
        ws_url="wss://example.invalid/ws",
        token_url="https://nls-meta.example/",
        ws_factory=_ws_factory(channel),  # type: ignore[arg-type]
        token_cache=cache,
    )


async def _collect(it: AsyncIterator) -> list:
    return [chunk async for chunk in it]


async def test_synthesize_streams_audio_then_terminal_chunk() -> None:
    """Binary frames become audio chunks; SynthesisCompleted yields a
    terminal is_final chunk."""
    channel = _FakeWSChannel([b"\x11\x22", b"\x33\x44", _synthesis_completed()])
    provider = _make_provider(channel)

    chunks = await _collect(provider.synthesize("先稳住别慌", voice="k-warm"))

    assert [c.audio for c in chunks[:2]] == [b"\x11\x22", b"\x33\x44"]
    assert chunks[-1].is_final is True
    # Exactly one terminal chunk.
    assert sum(1 for c in chunks if c.is_final) == 1


async def test_start_frame_carries_text_voice_and_appkey() -> None:
    channel = _FakeWSChannel([b"\x01", _synthesis_completed()])
    provider = _make_provider(channel)

    await _collect(provider.synthesize("你好", voice="k-warm", audio_format="mp3"))

    start = json.loads(channel.sent[0])
    assert start["header"]["name"] == "StartSynthesis"
    assert start["header"]["namespace"] == "SpeechSynthesizer"
    assert start["header"]["appkey"] == "app"
    assert start["payload"]["text"] == "你好"
    assert start["payload"]["voice"] == "xiaoyun"
    assert start["payload"]["format"] == "mp3"


async def test_token_is_appended_to_ws_url() -> None:
    channel = _FakeWSChannel([_synthesis_completed()])
    factory = _ws_factory(channel)
    cache = AliyunTokenCache(cached_token=AccessToken(token="tk", expires_at=9999999999))
    provider = AliyunTTSProvider(
        access_key_id="ak",
        access_key_secret="secret",
        app_key="app",
        ws_url="wss://example.invalid/ws",
        token_url="https://nls-meta.example/",
        ws_factory=factory,  # type: ignore[arg-type]
        token_cache=cache,
    )

    await _collect(provider.synthesize("hi", voice="k-warm"))

    assert "token=tk" in factory.state["last_url"]  # type: ignore[attr-defined]


async def test_task_failed_raises_upstream() -> None:
    channel = _FakeWSChannel([_task_failed("quota exceeded")])
    provider = _make_provider(channel)

    with pytest.raises(TTSUpstreamError):
        await _collect(provider.synthesize("hi", voice="k-warm"))


async def test_clean_close_before_completed_still_terminates() -> None:
    """A server close before SynthesisCompleted must still yield exactly
    one terminal chunk so the iteration contract holds."""
    channel = _FakeWSChannel([b"\xaa\xbb"])  # audio, then clean close
    provider = _make_provider(channel)

    chunks = await _collect(provider.synthesize("hi", voice="k-warm"))

    assert chunks[0].audio == b"\xaa\xbb"
    assert chunks[-1].is_final is True
    assert sum(1 for c in chunks if c.is_final) == 1


async def test_provider_name_is_stable() -> None:
    """`name="aliyun"` is what Langfuse + logs filter on; pin it."""
    channel = _FakeWSChannel([_synthesis_completed()])
    assert _make_provider(channel).name == "aliyun"


async def test_construction_captures_secrets() -> None:
    """Constructor signature is the API the factory depends on."""
    channel = _FakeWSChannel([_synthesis_completed()])
    provider = _make_provider(channel)
    assert provider._access_key_id == "ak"
    assert provider._app_key == "app"
    assert provider._ws_url.startswith("wss://")
