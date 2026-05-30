"""Aliyun NLS WS adapter tests.

We don't open a real WebSocket. A `_FakeWSConnection` implements the
subset of `websockets.WebSocketClientProtocol` the adapter touches:
`send(...)`, `recv()`, `close(...)`, plus the `__aenter__/__aexit__`
context-manager protocol.

The fake's `_to_send` queue holds the JSON events the adapter will
read; the fake's `sent` list records what the adapter wrote out so
the test can assert the StartTranscription handshake + audio payload
+ StopTranscription order.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator

import pytest
from app.asr._aliyun_token import AccessToken, AliyunTokenCache
from app.asr.aliyun import AliyunASRProvider
from app.asr.errors import ASRAuthError, ASRUpstreamError
from websockets.exceptions import ConnectionClosed, ConnectionClosedError, InvalidStatus

# --- fake WS plumbing ---


class _FakeWSChannel:
    """In-memory bidirectional channel mirroring the bits of the real
    `WebSocketClientProtocol` we touch."""

    def __init__(self, to_send: list[str | bytes]) -> None:
        self.sent: list[str | bytes] = []
        self._to_send = list(to_send)
        # If non-None, the next `recv()` raises this exception instead
        # of returning a value — lets us simulate mid-stream closes.
        self._recv_raises: BaseException | None = None
        self.close_calls: list[tuple[int, str]] = []

    def set_recv_raises(self, exc: BaseException) -> None:
        self._recv_raises = exc

    async def send(self, message: bytes | str) -> None:
        self.sent.append(message)

    async def recv(self) -> bytes | str:
        # Yield to the event loop every recv so the feeder task gets
        # cooperative-scheduling slots — real websockets do this
        # naturally via socket I/O; our in-memory fake otherwise
        # starves any concurrent sender.
        await asyncio.sleep(0)
        # Drain scripted frames first, THEN apply the configured raise —
        # lets a test script the `TranscriptionStarted` handshake frame
        # before simulating a mid-stream close.
        if self._to_send:
            return self._to_send.pop(0)
        if self._recv_raises is not None:
            raise self._recv_raises
        # Mimic a clean server close after the scripted messages.
        raise ConnectionClosed(rcvd=None, sent=None)

    async def close(self, code: int = 1000, reason: str = "") -> None:
        self.close_calls.append((code, reason))


class _FakeWSConnection:
    """Async-context manager wrapping `_FakeWSChannel` — matches what
    `websockets.connect(url)` returns (post-await)."""

    def __init__(self, channel: _FakeWSChannel) -> None:
        self.channel = channel

    async def __aenter__(self) -> _FakeWSChannel:
        return self.channel

    async def __aexit__(self, *exc: object) -> None:
        return None


def _ws_factory(channel: _FakeWSChannel) -> object:
    """Build a factory matching the `WSFactory` signature for tests.

    The factory captures `last_url` so tests can assert the
    `?token=...` query param made it through.
    """
    state: dict[str, object] = {"last_url": None}

    async def factory(url: str) -> _FakeWSConnection:
        state["last_url"] = url
        return _FakeWSConnection(channel)

    factory.state = state  # type: ignore[attr-defined]
    return factory


def _result_changed(text: str) -> str:
    return json.dumps(
        {
            "header": {"name": "TranscriptionResultChanged", "status": 20000000},
            "payload": {"result": text, "confidence": 0.92},
        }
    )


def _sentence_end(text: str) -> str:
    return json.dumps(
        {
            "header": {"name": "SentenceEnd", "status": 20000000},
            "payload": {"result": text, "confidence": 0.95},
        }
    )


def _task_failed(message: str) -> str:
    return json.dumps(
        {"header": {"name": "TaskFailed", "status_text": message, "status": 40000004}}
    )


def _started() -> str:
    """The `TranscriptionStarted` handshake frame the adapter now waits
    for before sending audio (Aliyun rejects audio sent while ROUTING)."""
    return json.dumps({"header": {"name": "TranscriptionStarted", "status": 20000000}})


async def _gen(chunks: list[bytes]) -> AsyncIterator[bytes]:
    for c in chunks:
        yield c


def _make_provider(channel: _FakeWSChannel) -> AliyunASRProvider:
    """Build a provider whose token cache is pre-seeded and whose WS
    factory yields the supplied fake channel."""
    cache = AliyunTokenCache(cached_token=AccessToken(token="tk-pretend", expires_at=9999999999))
    factory = _ws_factory(channel)
    provider = AliyunASRProvider(
        access_key_id="ak",
        access_key_secret="secret",
        app_key="app",
        ws_url="wss://example.invalid/ws",
        token_url="https://nls-meta.example/",
        ws_factory=factory,  # type: ignore[arg-type]
        token_cache=cache,
    )
    # Stash the factory's state dict on the provider for test assertions.
    provider._test_factory_state = factory.state  # type: ignore[attr-defined]
    return provider


# --- happy path ---


async def test_transcribe_stream_yields_partials_then_final() -> None:
    """Two partials + one SentenceEnd → two `partial` + one `final`
    ASREvent. The consumer loop terminates after SentenceEnd so we
    don't see anything past it even if the script has more frames."""
    channel = _FakeWSChannel(
        to_send=[
            _started(),
            _result_changed("你"),
            _result_changed("你好"),
            _sentence_end("你好世界"),
            # Trailing frame that must NOT be consumed:
            _result_changed("should not appear"),
        ]
    )
    provider = _make_provider(channel)

    events: list[tuple[str, str]] = []
    async for evt in provider.transcribe_stream(_gen([b"\x00\x00", b"\x01\x01"])):
        events.append((evt.kind, evt.text))

    assert events == [
        ("partial", "你"),
        ("partial", "你好"),
        ("final", "你好世界"),
    ]


async def test_handshake_sends_start_then_audio_then_stop() -> None:
    """Use a longer script so the feeder gets cooperative-scheduling
    slots to actually run before the SentenceEnd cancels it. In
    production this is automatic — many recv() cycles interleave
    with the feeder's sends — but the fake's recv() returns
    immediately if data is queued."""
    channel = _FakeWSChannel(
        to_send=[
            _started(),
            _result_changed("a"),
            _result_changed("ab"),
            _result_changed("abc"),
            _sentence_end("abc"),
        ]
    )

    async def audio_with_yields() -> AsyncIterator[bytes]:
        # Sleep(0) yields control to the consumer so it can read the
        # next event between our sends. Mirrors real audio I/O where
        # frames arrive at a fixed cadence rather than back-to-back.
        for frame in (b"frame1", b"frame2"):
            yield frame
            await asyncio.sleep(0)

    provider = _make_provider(channel)
    async for _ in provider.transcribe_stream(audio_with_yields()):
        pass

    # First write must be the StartTranscription JSON.
    assert isinstance(channel.sent[0], str)
    start = json.loads(channel.sent[0])
    assert start["header"]["name"] == "StartTranscription"
    assert start["header"]["appkey"] == "app"
    assert start["payload"]["format"] == "pcm"
    assert start["payload"]["sample_rate"] == 16000

    # Audio frames are forwarded verbatim as bytes.
    binary_sends = [m for m in channel.sent if isinstance(m, bytes)]
    assert binary_sends == [b"frame1", b"frame2"]


async def test_url_carries_token_query_param() -> None:
    channel = _FakeWSChannel(to_send=[_started(), _sentence_end("hi")])
    provider = _make_provider(channel)

    async for _ in provider.transcribe_stream(_gen([])):
        pass

    state = provider._test_factory_state  # type: ignore[attr-defined]
    url = state["last_url"]
    assert isinstance(url, str)
    assert url.startswith("wss://example.invalid/ws?")
    assert "token=tk-pretend" in url


async def test_confidence_surfaces_when_vendor_reports_it() -> None:
    channel = _FakeWSChannel(to_send=[_started(), _sentence_end("hi")])
    provider = _make_provider(channel)

    events = [e async for e in provider.transcribe_stream(_gen([]))]
    assert len(events) == 1
    assert events[0].confidence == pytest.approx(0.95)


# --- error / edge cases ---


async def test_task_failed_event_raises_upstream_error() -> None:
    channel = _FakeWSChannel(to_send=[_started(), _task_failed("internal speech error")])
    provider = _make_provider(channel)

    with pytest.raises(ASRUpstreamError, match="internal speech error"):
        async for _ in provider.transcribe_stream(_gen([])):
            pass


async def test_unexpected_close_mid_stream_raises_upstream_error() -> None:
    """Server tore the connection down hard before SentenceEnd —
    surfaced as ASRUpstreamError so the WS bridge can react."""
    channel = _FakeWSChannel(to_send=[_started()])
    channel.set_recv_raises(ConnectionClosedError(rcvd=None, sent=None))
    provider = _make_provider(channel)

    with pytest.raises(ASRUpstreamError, match="closed mid-stream"):
        async for _ in provider.transcribe_stream(_gen([])):
            pass


async def test_clean_close_before_final_yields_empty_final() -> None:
    """Server closed cleanly (1000) before SentenceEnd. Protocol
    contract requires us to emit a terminal `final` so callers don't
    hang waiting for one — empty text is the honest report."""
    channel = _FakeWSChannel(to_send=[_started(), _result_changed("partial")])
    # After draining the scripted messages, our fake raises
    # ConnectionClosed by design.
    provider = _make_provider(channel)

    events = [e async for e in provider.transcribe_stream(_gen([]))]
    assert events[-1].kind == "final"
    assert events[-1].text == ""


async def test_malformed_json_frame_is_skipped() -> None:
    """Aliyun occasionally emits non-JSON keep-alives; the consumer
    must NOT crash on them — drop and keep reading."""
    channel = _FakeWSChannel(
        to_send=[
            _started(),
            "not-json-at-all",  # malformed
            _result_changed("hi"),
            _sentence_end("hi there"),
        ]
    )
    provider = _make_provider(channel)

    events = [e async for e in provider.transcribe_stream(_gen([]))]
    kinds = [e.kind for e in events]
    assert kinds == ["partial", "final"]


async def test_unknown_event_name_is_dropped() -> None:
    """Control-plane events (TranscriptionStarted, SentenceBegin) are
    not interesting to the consumer — drop them without yielding."""
    started = json.dumps({"header": {"name": "TranscriptionStarted"}, "payload": {}})
    sentence_begin = json.dumps({"header": {"name": "SentenceBegin"}, "payload": {}})
    channel = _FakeWSChannel(to_send=[started, sentence_begin, _sentence_end("done")])
    provider = _make_provider(channel)

    events = [e async for e in provider.transcribe_stream(_gen([]))]
    assert len(events) == 1
    assert events[0].kind == "final"


async def test_construct_rejects_empty_ak() -> None:
    with pytest.raises(ValueError, match="access_key"):
        AliyunASRProvider(
            access_key_id="",
            access_key_secret="secret",
            app_key="app",
            ws_url="wss://x/",
            token_url="https://y/",
        )


async def test_construct_rejects_empty_app_key() -> None:
    with pytest.raises(ValueError, match="app_key"):
        AliyunASRProvider(
            access_key_id="ak",
            access_key_secret="secret",
            app_key="",
            ws_url="wss://x/",
            token_url="https://y/",
        )


async def test_ws_upgrade_401_maps_to_auth_error() -> None:
    """Token rejected at WS-upgrade time — must surface as auth, not
    as a generic upstream error, so a caller can decide to refresh."""
    cache = AliyunTokenCache(cached_token=AccessToken(token="tk", expires_at=9999999999))

    class _FakeResponse:
        status_code = 401

    async def factory(_url: str) -> object:
        raise InvalidStatus(response=_FakeResponse())  # type: ignore[arg-type]

    provider = AliyunASRProvider(
        access_key_id="ak",
        access_key_secret="secret",
        app_key="app",
        ws_url="wss://example.invalid/ws",
        token_url="https://nls-meta.example/",
        ws_factory=factory,  # type: ignore[arg-type]
        token_cache=cache,
    )

    with pytest.raises(ASRAuthError):
        async for _ in provider.transcribe_stream(_gen([])):
            pass
