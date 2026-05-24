"""whisper.cpp HTTP ASR adapter tests.

The real whisper-server binary isn't available in CI (and we wouldn't
want to ship a model file with the repo anyway), so we test against
an `httpx.AsyncClient` whose transport is a `httpx.MockTransport`.
The transport's handler closure captures what the adapter POSTs so
the test can assert on the multipart shape + the inferred URL.

What we cover here
------------------
* Happy path: one final event with whisper.cpp's `text` field.
* Empty utterance (zero bytes) short-circuits with `final, text=""`
  and never hits the network — the test asserts the handler was not
  called.
* Multipart shape: field name `file`, content-type `audio/wav`.
* Joined URL appends `/inference` to the configured base.
* Leading-space artifact stripped from the transcript.
* 401/403 → `ASRAuthError`; 4xx/5xx → `ASRUpstreamError` with status.
* Non-JSON body / missing `text` key / non-string `text` →
  `ASRMalformedResponseError`.
* `httpx.ConnectError` → `ASRUpstreamError`;
  `httpx.TimeoutException` → `ASRTimeoutError`.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

import httpx
import pytest
from app.asr import (
    ASRAuthError,
    ASREvent,
    ASRMalformedResponseError,
    ASRTimeoutError,
    ASRUpstreamError,
    WhisperCppASRProvider,
)

# --------------------------------------------------------------------- #
# Helpers                                                                #
# --------------------------------------------------------------------- #


async def _chunks(*pieces: bytes) -> AsyncIterator[bytes]:
    for piece in pieces:
        yield piece


def _make_provider(
    *,
    handler: Any,
    base_url: str = "http://whisper.local:8080",
) -> WhisperCppASRProvider:
    """Build a WhisperCppASRProvider wired to an `httpx.MockTransport`.

    The handler closure captures the request so individual tests can
    assert on URL / method / file payload.
    """
    transport = httpx.MockTransport(handler)

    def factory() -> httpx.AsyncClient:
        return httpx.AsyncClient(transport=transport)

    return WhisperCppASRProvider(base_url=base_url, http_client_factory=factory)


# --------------------------------------------------------------------- #
# Construction                                                           #
# --------------------------------------------------------------------- #


def test_provider_refuses_empty_base_url() -> None:
    """Misconfigured base URL must fail loudly at construction time —
    the factory's pre-check catches this too, but a defensive guard
    inside the adapter pins the contract."""
    with pytest.raises(ValueError, match="base_url"):
        WhisperCppASRProvider(base_url="")


def test_provider_name_is_stable() -> None:
    """Pin `name="whisper_cpp"` — surfaced in logs + Langfuse traces."""
    assert WhisperCppASRProvider(base_url="http://x").name == "whisper_cpp"


def test_provider_strips_trailing_slash_from_base_url() -> None:
    """`http://x/` and `http://x` must yield the same joined URL so a
    config typo doesn't double-slash the inference path."""
    provider = WhisperCppASRProvider(base_url="http://x.local/")
    assert provider._base_url == "http://x.local"


# --------------------------------------------------------------------- #
# Happy path                                                             #
# --------------------------------------------------------------------- #


async def test_transcribes_audio_to_final_event() -> None:
    """One POST, one JSON response, one `kind="final"` ASREvent."""
    captured: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["method"] = request.method
        captured["content_type"] = request.headers.get("content-type", "")
        captured["body"] = request.content
        return httpx.Response(200, json={"text": "你好世界"})

    provider = _make_provider(handler=handler)

    events = [event async for event in provider.transcribe_stream(_chunks(b"PCM-A", b"PCM-B"))]

    assert events == [ASREvent(kind="final", text="你好世界")]
    assert captured["method"] == "POST"
    assert captured["url"].endswith("/inference")
    assert captured["content_type"].startswith("multipart/form-data")
    # Body must contain the file field + the actual PCM bytes.
    assert b'name="file"' in captured["body"]
    assert b"PCM-APCM-B" in captured["body"]


async def test_transcript_leading_space_is_stripped() -> None:
    """whisper.cpp prepends a space artifact from the tokenizer; strip
    it so downstream char_count fields and log lines stay clean."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"text": " hello world"})

    provider = _make_provider(handler=handler)
    events = [event async for event in provider.transcribe_stream(_chunks(b"x"))]

    assert events == [ASREvent(kind="final", text="hello world")]


async def test_empty_utterance_short_circuits_without_http_call() -> None:
    """Zero audio bytes → final empty event, no network round-trip. The
    handler must not be invoked; if it is, the assertion in the
    closure fails the test."""
    handler_called = False

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal handler_called
        handler_called = True
        return httpx.Response(200, json={"text": "should not appear"})

    provider = _make_provider(handler=handler)
    events = [event async for event in provider.transcribe_stream(_chunks())]

    assert events == [ASREvent(kind="final", text="")]
    assert handler_called is False


async def test_only_empty_chunks_short_circuits_too() -> None:
    """A stream of zero-length chunks still counts as empty — the WS
    bridge feeds raw frames in and an idle mic produces these.
    Avoiding the round-trip saves a wasted HTTP call per silence."""
    handler_called = False

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal handler_called
        handler_called = True
        return httpx.Response(200, json={"text": "x"})

    provider = _make_provider(handler=handler)
    events = [event async for event in provider.transcribe_stream(_chunks(b"", b"", b""))]

    assert events == [ASREvent(kind="final", text="")]
    assert handler_called is False


async def test_inference_url_joins_base_and_path() -> None:
    """Base URL `http://host:8080` + path `/inference` → exact join,
    no double slashes."""
    captured_url: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured_url["url"] = str(request.url)
        return httpx.Response(200, json={"text": "ok"})

    provider = _make_provider(
        handler=handler,
        base_url="http://host.example:8080",
    )
    async for _ in provider.transcribe_stream(_chunks(b"x")):
        pass

    assert captured_url["url"] == "http://host.example:8080/inference"


# --------------------------------------------------------------------- #
# Error paths — typed mapping                                            #
# --------------------------------------------------------------------- #


async def test_401_maps_to_asr_auth_error() -> None:
    """A whisper.cpp deployment fronted by an auth proxy returns 401
    when the bearer is wrong. Pin the typed mapping so the router /
    Langfuse tagger can decide on the error class."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, text="unauthorized")

    provider = _make_provider(handler=handler)
    with pytest.raises(ASRAuthError):
        async for _ in provider.transcribe_stream(_chunks(b"x")):
            pass


async def test_403_maps_to_asr_auth_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(403, text="forbidden")

    provider = _make_provider(handler=handler)
    with pytest.raises(ASRAuthError):
        async for _ in provider.transcribe_stream(_chunks(b"x")):
            pass


async def test_5xx_maps_to_asr_upstream_error_with_status() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, text="service unavailable")

    provider = _make_provider(handler=handler)
    with pytest.raises(ASRUpstreamError) as ctx:
        async for _ in provider.transcribe_stream(_chunks(b"x")):
            pass
    assert ctx.value.status_code == 503


async def test_4xx_other_than_auth_maps_to_upstream_error() -> None:
    """A 422 (bad audio format, say) is the server's fault from our
    perspective — the request was well-formed at HTTP level. Still
    `ASRUpstreamError` with `status_code` so analysts can bucket it."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(422, text="bad audio")

    provider = _make_provider(handler=handler)
    with pytest.raises(ASRUpstreamError) as ctx:
        async for _ in provider.transcribe_stream(_chunks(b"x")):
            pass
    assert ctx.value.status_code == 422


async def test_non_json_body_maps_to_malformed_response() -> None:
    """A whisper-server build that returns plain text on success is a
    drift signal — surface it loudly so ops can pin the build version."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="hello world (plain)")

    provider = _make_provider(handler=handler)
    with pytest.raises(ASRMalformedResponseError):
        async for _ in provider.transcribe_stream(_chunks(b"x")):
            pass


async def test_missing_text_field_maps_to_malformed_response() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"segments": []})

    provider = _make_provider(handler=handler)
    with pytest.raises(ASRMalformedResponseError):
        async for _ in provider.transcribe_stream(_chunks(b"x")):
            pass


async def test_non_string_text_field_maps_to_malformed_response() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"text": 42})

    provider = _make_provider(handler=handler)
    with pytest.raises(ASRMalformedResponseError):
        async for _ in provider.transcribe_stream(_chunks(b"x")):
            pass


async def test_connect_error_maps_to_upstream_error() -> None:
    """A whisper-server box that's down → `httpx.ConnectError` → typed
    `ASRUpstreamError` so the WS bridge classifies it the same as any
    other vendor outage."""

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused")

    provider = _make_provider(handler=handler)
    with pytest.raises(ASRUpstreamError):
        async for _ in provider.transcribe_stream(_chunks(b"x")):
            pass


async def test_timeout_maps_to_asr_timeout_error() -> None:
    """A slow whisper-server (CPU-bound on a long audio) → typed
    timeout. Distinct from generic upstream so failover policies can
    apply a longer retry budget for transient slow-decode."""

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("slow decode")

    provider = _make_provider(handler=handler)
    with pytest.raises(ASRTimeoutError):
        async for _ in provider.transcribe_stream(_chunks(b"x")):
            pass
