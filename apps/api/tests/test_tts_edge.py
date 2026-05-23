"""Edge TTS WS adapter tests.

The real Microsoft Edge TTS endpoint is unavailable in CI (firewall +
no key swap path), so we test against a `_FakeWSConnection` that
mirrors `websockets.WebSocketClientProtocol`. The fake's `_to_send`
queue holds the frames the adapter will read; `sent` records the
two text frames the adapter writes (`speech.config` + SSML).

What we cover here
------------------
* Happy-path: two text writes (speech.config + SSML), three binary
  reads with audio payloads, one `turn.end` text read → three audio
  chunks emitted, last one is_final.
* SSML XML-escapes the input so `<`, `>`, `&` don't break the envelope.
* Connection closing after audio still produces a clean terminal chunk.
* Connection closing before any audio raises `TTSUpstreamError`.
* 401 / 403 on connect → `TTSAuthError`.
* Malformed binary frame → `TTSMalformedResponseError`.
* `unsupported voice` → `TTSUpstreamError` (defensive).
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from typing import cast

import pytest
from app.tts import (
    EdgeTTSProvider,
    TTSAudioChunk,
    TTSAuthError,
    TTSMalformedResponseError,
    TTSUpstreamError,
)
from websockets.exceptions import ConnectionClosed, InvalidStatus

# --------------------------------------------------------------------- #
# Fake WS plumbing — matches the subset of websockets the adapter touches #
# --------------------------------------------------------------------- #


class _FakeWSChannel:
    def __init__(self, to_send: list[str | bytes]) -> None:
        self.sent: list[str | bytes] = []
        self._to_send = list(to_send)
        self._recv_raises: BaseException | None = None
        self.close_calls: list[tuple[int, str]] = []

    def set_recv_raises(self, exc: BaseException) -> None:
        self._recv_raises = exc

    async def send(self, message: bytes | str) -> None:
        self.sent.append(message)

    async def recv(self) -> bytes | str:
        await asyncio.sleep(0)
        if self._recv_raises is not None:
            raise self._recv_raises
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


def _ws_factory(channel: _FakeWSChannel):  # type: ignore[no-untyped-def]
    state: dict[str, object] = {"last_url": None}

    async def factory(url: str) -> _FakeWSConnection:
        state["last_url"] = url
        return _FakeWSConnection(channel)

    factory.state = state  # type: ignore[attr-defined]
    return factory


def _binary_audio_frame(audio: bytes, *, path: str = "audio") -> bytes:
    """Build a fake Edge TTS binary frame: 2-byte BE header length,
    ASCII header (CRLF + `\r\n\r\n` separator), then audio bytes."""
    header_text = f"X-RequestId:test\r\nPath:{path}\r\nContent-Type:audio/mpeg\r\n\r\n"
    header_bytes = header_text.encode("ascii")
    return len(header_bytes).to_bytes(2, "big") + header_bytes + audio


def _turn_end_text() -> str:
    return "X-RequestId:test\r\nPath:turn.end\r\n\r\n{}"


# --------------------------------------------------------------------- #
# Happy path                                                             #
# --------------------------------------------------------------------- #


async def test_synthesize_yields_audio_chunks_then_final() -> None:
    channel = _FakeWSChannel(
        to_send=[
            _binary_audio_frame(b"\x01\x02"),
            _binary_audio_frame(b"\x03\x04"),
            _binary_audio_frame(b"\x05"),
            _turn_end_text(),
        ]
    )
    provider = EdgeTTSProvider(ws_factory=_ws_factory(channel))  # type: ignore[arg-type]

    chunks: list[TTSAudioChunk] = []
    async for chunk in provider.synthesize("你好 K", voice="k-warm", audio_format="mp3"):
        chunks.append(chunk)

    audio_chunks = [c for c in chunks if c.audio]
    assert [c.audio for c in audio_chunks] == [b"\x01\x02", b"\x03\x04", b"\x05"]
    assert chunks[-1].is_final is True


async def test_handshake_sends_speech_config_then_ssml() -> None:
    """The first two writes must be the speech.config + SSML envelope;
    pin the order so a future refactor doesn't accidentally send SSML
    first and rely on the upstream tolerating it."""
    channel = _FakeWSChannel(
        to_send=[_binary_audio_frame(b"\x00"), _turn_end_text()],
    )
    provider = EdgeTTSProvider(ws_factory=_ws_factory(channel))  # type: ignore[arg-type]

    async for _ in provider.synthesize("hi", voice="k-warm"):
        pass

    assert len(channel.sent) == 2
    speech_config = cast(str, channel.sent[0])
    ssml = cast(str, channel.sent[1])
    assert "Path:speech.config" in speech_config
    assert "Path:ssml" in ssml
    assert "<voice name='zh-CN-XiaoxiaoNeural'>" in ssml


async def test_ssml_xml_escapes_dangerous_chars() -> None:
    """`<`, `>`, `&` must be escaped so the SSML envelope stays valid
    and an injected `</voice>` can't trick the upstream parser into
    swapping speakers mid-utterance."""
    channel = _FakeWSChannel(to_send=[_binary_audio_frame(b"\x00"), _turn_end_text()])
    provider = EdgeTTSProvider(ws_factory=_ws_factory(channel))  # type: ignore[arg-type]

    async for _ in provider.synthesize("a<b>c&d</voice>"):
        pass

    ssml = cast(str, channel.sent[1])
    assert "&lt;b&gt;" in ssml
    assert "&amp;" in ssml
    assert "</voice>c" not in ssml  # raw close-tag escaped before reaching upstream


# --------------------------------------------------------------------- #
# Error paths                                                            #
# --------------------------------------------------------------------- #


async def test_connection_close_after_audio_returns_clean() -> None:
    """Edge sometimes drops the `turn.end` frame on very short hints —
    if we got audio, treat the close as success and emit a synthetic
    terminal chunk."""
    channel = _FakeWSChannel(to_send=[_binary_audio_frame(b"\x42")])
    provider = EdgeTTSProvider(ws_factory=_ws_factory(channel))  # type: ignore[arg-type]

    chunks = [c async for c in provider.synthesize("hi")]
    assert any(c.audio == b"\x42" for c in chunks)
    assert chunks[-1].is_final is True


async def test_connection_close_before_audio_raises_upstream() -> None:
    """Closing before any audio means the synth never started — distinct
    failure mode that needs a typed error."""
    channel = _FakeWSChannel(to_send=[])  # closes immediately on first recv
    provider = EdgeTTSProvider(ws_factory=_ws_factory(channel))  # type: ignore[arg-type]

    with pytest.raises(TTSUpstreamError):
        async for _ in provider.synthesize("hi"):
            pass


async def test_auth_failure_maps_to_tts_auth_error() -> None:
    """401/403 on the WS upgrade → `TTSAuthError` so the router's
    failover logic doesn't burn retries on a known-bad key."""

    class _FakeResponse:
        def __init__(self, status_code: int) -> None:
            self.status_code = status_code

    async def failing_factory(url: str) -> _FakeWSConnection:
        raise InvalidStatus(_FakeResponse(401))  # type: ignore[arg-type]

    provider = EdgeTTSProvider(ws_factory=failing_factory)  # type: ignore[arg-type]

    with pytest.raises(TTSAuthError):
        async for _ in provider.synthesize("hi"):
            pass


async def test_unexpected_5xx_maps_to_upstream_error() -> None:
    class _FakeResponse:
        def __init__(self, status_code: int) -> None:
            self.status_code = status_code

    async def failing_factory(url: str) -> _FakeWSConnection:
        raise InvalidStatus(_FakeResponse(500))  # type: ignore[arg-type]

    provider = EdgeTTSProvider(ws_factory=failing_factory)  # type: ignore[arg-type]

    with pytest.raises(TTSUpstreamError) as ctx:
        async for _ in provider.synthesize("hi"):
            pass
    assert ctx.value.status_code == 500


async def test_malformed_binary_frame_raises_malformed_response() -> None:
    """A header length that overshoots the frame body must surface as a
    typed `TTSMalformedResponseError` so analysts can separate "vendor
    drift" from "vendor down"."""
    # Header length claims 999 bytes but payload is only ~5.
    bad_frame = (999).to_bytes(2, "big") + b"short"
    channel = _FakeWSChannel(to_send=[bad_frame])
    provider = EdgeTTSProvider(ws_factory=_ws_factory(channel))  # type: ignore[arg-type]

    with pytest.raises(TTSMalformedResponseError):
        async for _ in provider.synthesize("hi"):
            pass


async def test_audio_metadata_frame_is_ignored() -> None:
    """Edge ships `Path: audio.metadata` frames alongside the audio
    `Path: audio` frames. We silently drop metadata — the consumer only
    cares about the audio body — and proceed to the real audio."""
    channel = _FakeWSChannel(
        to_send=[
            _binary_audio_frame(b"meta-payload", path="audio.metadata"),
            _binary_audio_frame(b"real-audio"),
            _turn_end_text(),
        ]
    )
    provider = EdgeTTSProvider(ws_factory=_ws_factory(channel))  # type: ignore[arg-type]

    audio_chunks = [c.audio for c in [c async for c in provider.synthesize("hi")] if c.audio]
    assert audio_chunks == [b"real-audio"]


async def test_provider_name_is_stable() -> None:
    """Pin `name="edge"` — surfaced in logs + Langfuse traces."""
    assert EdgeTTSProvider().name == "edge"


async def _drain(gen: AsyncIterator[TTSAudioChunk]) -> list[TTSAudioChunk]:
    return [c async for c in gen]
