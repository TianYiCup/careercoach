"""Aliyun NLS streaming ASR adapter (A-28).

Wire protocol
-------------
Aliyun NLS realtime ASR is a WebSocket service. After URL-level token
auth (`?token=...`), the client sends one JSON `StartTranscription`
control message, then streams raw PCM-16kHz-mono audio frames as
binary WS messages, and finally a `StopTranscription` control message.
The server emits JSON events as text WS messages:

    TranscriptionStarted        — handshake ack
    TranscriptionResultChanged  — partial transcript (revisable)
    SentenceBegin / SentenceEnd — utterance boundaries (we use SentenceEnd
                                  as the canonical 'final' signal)
    TranscriptionCompleted      — server acknowledged Stop
    TaskFailed                  — error path (map to ASRUpstreamError)

We deliberately fold the vendor-specific event taxonomy down into our
two-state `ASREvent(kind="partial"|"final")` envelope so the WS bridge
(`/v1/copilot/sessions/{id}/stream`) doesn't need to know whether the
ASR vendor speaks "SentenceEnd" or "is_final": both surfaces.

Concurrency
-----------
For each utterance we spawn TWO asyncio tasks — one feeds audio out,
one consumes events in — and join on the consumer. The feeder is
cancelled when the consumer exits. Inverting this (single task that
sends then reads) would deadlock against Aliyun's behaviour of
emitting partials WHILE the client is still sending audio.

Testability
-----------
`AliyunASRProvider.__init__` takes a `ws_factory` callable that
defaults to `websockets.connect`. Tests pass a stub that yields a
fake bidirectional channel so we don't need a real socket or live
Aliyun account in CI.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from typing import Any, Protocol

import structlog
from websockets import connect as ws_connect
from websockets.exceptions import (
    ConnectionClosed,
    ConnectionClosedError,
    InvalidStatus,
    WebSocketException,
)

from app.asr._aliyun_token import AliyunTokenCache
from app.asr.errors import (
    ASRAuthError,
    ASRTimeoutError,
    ASRUpstreamError,
)
from app.asr.provider import DEFAULT_TIMEOUT_SECONDS, ASREvent

logger = structlog.get_logger(__name__)

PROVIDER_NAME = "aliyun"

# Aliyun NLS realtime ASR fixed audio format. Caller is expected to
# resample / re-encode to this shape upstream (the copilot WS bridge
# already standardises on 16k PCM for the same reason).
_AUDIO_SAMPLE_RATE = 16000
_AUDIO_FORMAT = "pcm"

# Vendor event kind strings — pulled to module constants so a typo in
# the dispatch table fails at import time, not runtime.
_EVT_STARTED = "TranscriptionStarted"
_EVT_RESULT_CHANGED = "TranscriptionResultChanged"
_EVT_SENTENCE_END = "SentenceEnd"
_EVT_TASK_FAILED = "TaskFailed"

# Aliyun reaps a WS session that sits idle (no audio, or the post-Stop
# wait when a segment carried no speech) with a `TaskFailed` whose
# `status_text` carries this marker, e.g.
#   "Gateway:IDLE_TIMEOUT:Websocket session is idle for too long time,
#    the last directive is 'StopTranscription'!"
# For the copilot's segment-per-utterance model that just means "no
# speech this segment" — equivalent to a clean close. We must NOT surface
# it as an error: the WS bridge maps any ASRError to a user-facing
# "transcription unavailable", so a natural conversation pause would look
# like an outage. Treated as a benign empty final instead.
_IDLE_TIMEOUT_MARKER = "IDLE_TIMEOUT"


class _WSChannel(Protocol):
    """Subset of `websockets.WebSocketClientProtocol` we touch.

    Stated explicitly so the test stub doesn't have to inherit from
    the real type. Matches v13's async-iterator protocol.
    """

    async def send(self, message: bytes | str) -> None: ...
    async def recv(self) -> bytes | str: ...
    async def close(self, code: int = 1000, reason: str = "") -> None: ...


WSFactory = Callable[[str], Awaitable["_WSConnection"]]
"""Factory: URL -> awaited connection (an async context manager)."""


async def _default_ws_factory(url: str) -> _WSConnection:
    """Real-websockets factory.

    `websockets.connect(url)` returns a `Connect` that is itself an
    async context manager — awaiting it would yield the bare protocol,
    which is NOT a context manager. So we return the unawaited `Connect`
    and let the caller `async with` it. This matches the `WSFactory`
    contract the in-memory test fakes implement.
    """
    return ws_connect(url)  # type: ignore[return-value]


class _WSConnection(Protocol):
    """The awaitable returned by `websockets.connect` exposes both an
    async context-manager AND a plain coroutine interface. We use the
    latter so the adapter owns the close lifetime explicitly."""

    async def __aenter__(self) -> _WSChannel: ...
    async def __aexit__(self, *exc: object) -> None: ...


class AliyunASRProvider:
    """Streaming ASR client for Aliyun NLS realtime.

    Structurally implements `app.asr.provider.ASRProvider`. Holds a
    long-lived token cache so we don't re-sign on every utterance;
    the cache auto-refreshes 30s before the token's stated expiry.
    """

    name = PROVIDER_NAME

    def __init__(
        self,
        *,
        access_key_id: str,
        access_key_secret: str,
        app_key: str,
        ws_url: str,
        token_url: str,
        ws_factory: WSFactory | None = None,
        token_cache: AliyunTokenCache | None = None,
    ) -> None:
        if not access_key_id or not access_key_secret:
            raise ValueError("aliyun ASR: access_key_id / access_key_secret must be set")
        if not app_key:
            raise ValueError("aliyun ASR: app_key must be set")
        self._access_key_id = access_key_id
        self._access_key_secret = access_key_secret
        self._app_key = app_key
        self._ws_url = ws_url
        self._token_url = token_url
        # Default factory returns the unawaited Connect (an async CM) —
        # tests inject a stub matching the same contract. Awaiting
        # `connect()` would yield the bare protocol, which is not a CM.
        self._ws_factory: WSFactory = ws_factory or _default_ws_factory
        self._token_cache = token_cache or AliyunTokenCache()

    async def transcribe_stream(
        self,
        audio_chunks: AsyncIterator[bytes],
        *,
        timeout: float = DEFAULT_TIMEOUT_SECONDS,
    ) -> AsyncIterator[ASREvent]:
        """Stream PCM frames in, yield ASREvents out.

        The async generator is the public surface; everything below
        is plumbing. We isolate the consumer here so failures inside
        the feeder task surface as cancellation on the consumer's
        side, mapped to typed ASRError.
        """
        token = await self._token_cache.get(
            access_key_id=self._access_key_id,
            access_key_secret=self._access_key_secret,
            endpoint_url=self._token_url,
            timeout_s=timeout,
        )
        url = self._build_ws_url(token.token)
        task_id = uuid.uuid4().hex

        try:
            connection = await self._ws_factory(url)
        except InvalidStatus as exc:
            raise _map_status_error(exc) from exc
        except TimeoutError as exc:
            raise ASRTimeoutError(
                f"aliyun ASR connect exceeded {timeout}s",
                provider=PROVIDER_NAME,
            ) from exc
        except WebSocketException as exc:
            raise ASRUpstreamError(
                f"aliyun ASR connect failed: {exc}",
                provider=PROVIDER_NAME,
            ) from exc

        async with connection as channel:
            await channel.send(_start_payload(task_id=task_id, app_key=self._app_key))
            # Aliyun NLS rejects audio sent before it acknowledges the
            # StartTranscription with a `TranscriptionStarted` event
            # ("Invalid binary message while server state is 'ROUTING'").
            # Block the feeder until that handshake completes.
            await _await_started(channel, task_id=task_id)
            feeder: asyncio.Task[None] = asyncio.create_task(
                _feed_audio(
                    channel,
                    audio_chunks=audio_chunks,
                    task_id=task_id,
                    app_key=self._app_key,
                ),
                name="aliyun-asr-feeder",
            )
            try:
                async for event in _consume_events(channel, task_id=task_id):
                    yield event
            finally:
                # Stop the feeder regardless of how the consumer exited
                # (clean final, exception, caller broke out early).
                if not feeder.done():
                    feeder.cancel()
                    with contextlib.suppress(asyncio.CancelledError, Exception):
                        await feeder

    def _build_ws_url(self, token: str) -> str:
        sep = "&" if "?" in self._ws_url else "?"
        return f"{self._ws_url}{sep}token={token}"


async def _await_started(channel: _WSChannel, *, task_id: str) -> None:
    """Block until the server emits `TranscriptionStarted`.

    Aliyun NLS is a strict state machine: audio frames sent before the
    server acknowledges `StartTranscription` are rejected with
    `MESSAGE_INVALID ... server state is 'ROUTING'`. We consume control
    frames until `TranscriptionStarted` arrives (drop other control
    noise), raising on an early `TaskFailed` or a premature close.
    """
    while True:
        try:
            raw = await channel.recv()
        except ConnectionClosedError as exc:
            raise ASRUpstreamError(
                f"aliyun ASR closed before start: {exc}",
                provider=PROVIDER_NAME,
            ) from exc
        except ConnectionClosed as exc:
            raise ASRUpstreamError(
                "aliyun ASR closed before TranscriptionStarted",
                provider=PROVIDER_NAME,
            ) from exc

        parsed = _parse_event(raw, task_id=task_id)
        if parsed is None:
            continue
        name, payload = parsed
        if name == _EVT_STARTED:
            return
        if name == _EVT_TASK_FAILED:
            header = payload.get("header", {})
            message = header.get("status_text", "task failed")
            status_code = header.get("status")
            raise ASRUpstreamError(
                f"aliyun ASR task failed before start: {message}",
                provider=PROVIDER_NAME,
                status_code=int(status_code) if isinstance(status_code, int) else None,
            )
        # Other control frames before start — drop and keep waiting.


async def _feed_audio(
    channel: _WSChannel,
    *,
    audio_chunks: AsyncIterator[bytes],
    task_id: str,
    app_key: str,
) -> None:
    """Pump caller-supplied PCM frames into the WS, then send Stop.

    Aliyun expects audio frames as raw binary WS messages. The
    `StopTranscription` control message is sent on graceful EOF so
    the server emits a terminal `TranscriptionCompleted`.

    Any exception during send is logged and swallowed — the consumer
    side will surface the resulting `ConnectionClosed` instead, which
    is the better error to propagate to the caller.
    """
    try:
        async for chunk in audio_chunks:
            if not chunk:
                continue
            await channel.send(chunk)
        await channel.send(_stop_payload(task_id=task_id, app_key=app_key))
    except ConnectionClosed:
        # Server tore the connection down — consumer will see this
        # too via its own recv() and raise the right typed error.
        return
    except Exception:
        logger.exception("aliyun_asr_feeder_failed", task_id=task_id)


async def _consume_events(
    channel: _WSChannel,
    *,
    task_id: str,
) -> AsyncIterator[ASREvent]:
    """Read JSON events from the WS, yield typed ASREvents.

    Vendor events the consumer cares about:

      * TranscriptionResultChanged → ASREvent(kind="partial")
      * SentenceEnd                → ASREvent(kind="final"); we also
                                     terminate the loop here so a single
                                     utterance yields exactly one final
      * TaskFailed                 → raise ASRUpstreamError with the
                                     vendor's message

    Other event types (TranscriptionStarted, SentenceBegin,
    TranscriptionCompleted) are control-plane noise to the caller
    and are silently dropped.
    """
    while True:
        try:
            raw = await channel.recv()
        except ConnectionClosedError as exc:
            raise ASRUpstreamError(
                f"aliyun ASR connection closed mid-stream: {exc}",
                provider=PROVIDER_NAME,
            ) from exc
        except ConnectionClosed:
            # Clean close before we saw a SentenceEnd → treat as a
            # zero-content final so the caller still gets the terminal
            # event the Protocol contract requires.
            yield ASREvent(kind="final", text="")
            return

        event = _parse_event(raw, task_id=task_id)
        if event is None:
            continue
        kind, payload = event
        if kind == _EVT_RESULT_CHANGED:
            yield ASREvent(
                kind="partial",
                text=_extract_text(payload),
                confidence=_extract_confidence(payload),
            )
        elif kind == _EVT_SENTENCE_END:
            yield ASREvent(
                kind="final",
                text=_extract_text(payload),
                confidence=_extract_confidence(payload),
            )
            return
        elif kind == _EVT_TASK_FAILED:
            header = payload.get("header", {})
            message = header.get("status_text", "task failed")
            status_code = header.get("status")
            if _is_idle_timeout(message):
                # Benign: the gateway reaped an idle session (no speech
                # this segment). Mirror the clean-close path — emit an
                # empty final and stop, so a natural pause is a no-op turn
                # rather than a user-facing "transcription unavailable".
                logger.info(
                    "aliyun_asr_idle_timeout",
                    task_id=task_id,
                    status_text=message,
                )
                yield ASREvent(kind="final", text="")
                return
            raise ASRUpstreamError(
                f"aliyun ASR task failed: {message}",
                provider=PROVIDER_NAME,
                status_code=int(status_code) if isinstance(status_code, int) else None,
            )
        # Unknown / control event — drop.


def _is_idle_timeout(status_text: object) -> bool:
    """True when a `TaskFailed` status_text reports an idle-session reap
    (e.g. `Gateway:IDLE_TIMEOUT:...`). Robust to a non-string payload."""
    return isinstance(status_text, str) and _IDLE_TIMEOUT_MARKER in status_text


def _parse_event(
    raw: bytes | str,
    *,
    task_id: str,
) -> tuple[str, dict[str, Any]] | None:
    """Lift one WS frame into `(event_name, full_payload)`.

    Returns `None` for malformed frames so the consumer loop can skip
    them rather than blow up the whole stream — Aliyun does sometimes
    send keep-alives or status frames that aren't strictly JSON.
    """
    if isinstance(raw, bytes):
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError:
            logger.warning("aliyun_asr_undecodable_frame", task_id=task_id)
            return None
    else:
        text = raw
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        logger.warning("aliyun_asr_non_json_frame", task_id=task_id, sample=text[:80])
        return None
    if not isinstance(payload, dict):
        return None
    header = payload.get("header")
    if not isinstance(header, dict):
        return None
    name = header.get("name")
    if not isinstance(name, str):
        return None
    return name, payload


def _extract_text(payload: dict[str, Any]) -> str:
    """Pull the transcript text out of a NLS event payload.

    NLS puts the transcript under `payload.result`. Defensive against
    missing keys because partial events occasionally arrive before
    the first byte of text is decoded.
    """
    body = payload.get("payload")
    if isinstance(body, dict):
        result = body.get("result")
        if isinstance(result, str):
            return result
    return ""


def _extract_confidence(payload: dict[str, Any]) -> float | None:
    """Pull the per-event confidence score (0..1). None when absent."""
    body = payload.get("payload")
    if isinstance(body, dict):
        confidence = body.get("confidence")
        if isinstance(confidence, (int, float)):
            return float(confidence)
    return None


def _start_payload(*, task_id: str, app_key: str) -> str:
    return json.dumps(
        {
            "header": {
                "message_id": uuid.uuid4().hex,
                "task_id": task_id,
                "namespace": "SpeechTranscriber",
                "name": "StartTranscription",
                "appkey": app_key,
            },
            "payload": {
                "format": _AUDIO_FORMAT,
                "sample_rate": _AUDIO_SAMPLE_RATE,
                "enable_intermediate_result": True,
                "enable_punctuation_prediction": True,
                "enable_inverse_text_normalization": True,
            },
        }
    )


def _stop_payload(*, task_id: str, app_key: str) -> str:
    # Aliyun requires the appkey on StopTranscription too — omitting it
    # fails the whole utterance with "Missing message appkey!".
    return json.dumps(
        {
            "header": {
                "message_id": uuid.uuid4().hex,
                "task_id": task_id,
                "namespace": "SpeechTranscriber",
                "name": "StopTranscription",
                "appkey": app_key,
            }
        }
    )


def _map_status_error(exc: InvalidStatus) -> Exception:
    """HTTP-status -> typed ASRError. Aliyun returns 401/403 for an
    expired or revoked token, everything else is upstream-flavoured."""
    status_code = exc.response.status_code
    if status_code in (401, 403):
        return ASRAuthError(
            f"aliyun ASR auth failed (ws upgrade {status_code})",
            provider=PROVIDER_NAME,
        )
    return ASRUpstreamError(
        f"aliyun ASR ws upgrade failed ({status_code})",
        provider=PROVIDER_NAME,
        status_code=status_code,
    )


__all__ = ["AliyunASRProvider"]
