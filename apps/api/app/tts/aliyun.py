"""Aliyun NLS streaming TTS adapter.

The wire protocol mirrors `app.asr.aliyun` (NLS realtime ASR): same
authentication (account AK + per-project AppKey), same WebSocket
endpoint family (`wss://nls-gateway-…/ws/v1`), same JSON control-frame
shape. The directions are reversed — we send one `StartSynthesis`
control frame carrying the text, and the server streams **binary audio
frames** back, terminated by a `SynthesisCompleted` text frame.

Protocol — SpeechSynthesizer namespace
--------------------------------------
    Client → Server
        text frame:   JSON `StartSynthesis` (text + voice + format)

    Server → Client
        text frame:   `SynthesisStarted`   — control, ignored
        binary frame: a chunk of encoded audio (mp3/wav per format)
        text frame:   `MetaInfo`           — timing metadata, ignored
        text frame:   `SynthesisCompleted` — terminal; close the stream
        text frame:   `TaskFailed`         — raise TTSUpstreamError

Auth reuses the shared `AliyunTokenCache` from the ASR side so a copilot
session that already minted a token for transcription doesn't re-sign
for synthesis.
"""

from __future__ import annotations

import json
import time
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
from app.tts.errors import (
    TTSAuthError,
    TTSTimeoutError,
    TTSUpstreamError,
)
from app.tts.provider import (
    DEFAULT_AUDIO_FORMAT,
    DEFAULT_TIMEOUT_SECONDS,
    DEFAULT_VOICE,
    TTSAudioChunk,
    TTSAudioFormat,
    TTSVoice,
)

logger = structlog.get_logger(__name__)

PROVIDER_NAME = "aliyun"

# Aliyun NLS TTS output sample rate. 16 kHz matches the copilot audio
# surface and keeps mp3 payloads small for the headphone hint loop.
_AUDIO_SAMPLE_RATE = 16000

# Voice catalog mapping the stable API-side voice id to the Aliyun NLS
# voice name. `xiaoyun` is Aliyun's warm Mandarin female — the closest
# match to the Edge `Xiaoxiao` voice so a primary→backup failover
# doesn't change the perceived speaker mid-session.
_VOICE_CATALOG: dict[TTSVoice, str] = {
    "k-warm": "xiaoyun",
}

# Our `TTSAudioFormat` → the string Aliyun NLS expects in the payload.
_FORMAT_CATALOG: dict[TTSAudioFormat, str] = {
    "mp3": "mp3",
    "wav": "wav",
    "ogg": "mp3",  # NLS has no ogg; mp3 is the safe lowest-common default
}

# Vendor event names (text frames). Pulled to constants so a typo fails
# at import, not mid-stream.
_EVT_STARTED = "SynthesisStarted"
_EVT_COMPLETED = "SynthesisCompleted"
_EVT_TASK_FAILED = "TaskFailed"


class _WSChannel(Protocol):
    """Subset of `websockets.WebSocketClientProtocol` we touch.

    Stated explicitly so the test stub doesn't have to inherit from the
    real type. Matches v13's async-iterator protocol.
    """

    async def send(self, message: bytes | str) -> None: ...
    async def recv(self) -> bytes | str: ...
    async def close(self, code: int = 1000, reason: str = "") -> None: ...


class _WSConnection(Protocol):
    """The awaitable returned by `websockets.connect`, used as an async
    context manager so the adapter owns close lifetime explicitly."""

    async def __aenter__(self) -> _WSChannel: ...
    async def __aexit__(self, *exc: object) -> None: ...


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


class AliyunTTSProvider:
    """Streaming TTS client for Aliyun NLS realtime.

    Structurally implements `app.tts.provider.TTSProvider`. Holds a
    long-lived token cache so we don't re-sign on every synthesis; the
    cache auto-refreshes shortly before the token's stated expiry.
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
        self._access_key_id = access_key_id
        self._access_key_secret = access_key_secret
        self._app_key = app_key
        self._ws_url = ws_url
        self._token_url = token_url
        # Default factory returns the unawaited Connect (an async CM) —
        # tests inject a stub matching the same contract.
        self._ws_factory: WSFactory = ws_factory or _default_ws_factory
        self._token_cache = token_cache or AliyunTokenCache()

    async def synthesize(
        self,
        text: str,
        *,
        voice: TTSVoice = DEFAULT_VOICE,
        audio_format: TTSAudioFormat = DEFAULT_AUDIO_FORMAT,
        timeout: float = DEFAULT_TIMEOUT_SECONDS,
    ) -> AsyncIterator[TTSAudioChunk]:
        vendor_voice = _VOICE_CATALOG.get(voice)
        if vendor_voice is None:
            # Defensive — the Literal type rules this out at type-check
            # time, but a future voice added to TTSVoice without a
            # catalog entry would otherwise send an empty payload.
            raise TTSUpstreamError(
                f"unsupported voice for aliyun backend: {voice!r}",
                provider=self.name,
            )
        vendor_format = _FORMAT_CATALOG.get(audio_format, _FORMAT_CATALOG["mp3"])
        task_id = uuid.uuid4().hex
        logger.info(
            "tts_aliyun_synthesize_started",
            voice=voice,
            vendor_voice=vendor_voice,
            audio_format=audio_format,
            char_count=len(text),
            task_id=task_id,
        )

        token = await self._token_cache.get(
            access_key_id=self._access_key_id,
            access_key_secret=self._access_key_secret,
            endpoint_url=self._token_url,
            timeout_s=timeout,
        )
        url = self._build_ws_url(token.token)

        try:
            connection = await self._ws_factory(url)
        except InvalidStatus as exc:
            raise _map_status_error(exc) from exc
        except TimeoutError as exc:
            raise TTSTimeoutError(
                f"aliyun tts connect exceeded {timeout}s",
                provider=self.name,
            ) from exc
        except WebSocketException as exc:
            raise TTSUpstreamError(
                f"aliyun tts connect failed: {exc}",
                provider=self.name,
            ) from exc

        # F2-0: isolate the per-hint vendor WS handshake from the synthesis
        # itself. The route's `tts_synth_latency.first_chunk_ms` bundles
        # both; this `connect_ms` line lets a dashboard see what fraction is
        # the handshake — i.e. how much a per-session connection reuse (F2)
        # could actually save. `connect_started` is set after the token
        # fetch so the number is the WS upgrade alone, not token I/O.
        connect_started = time.perf_counter()
        async with connection as channel:
            logger.info(
                "tts_connect_latency",
                provider=self.name,
                task_id=task_id,
                connect_ms=round((time.perf_counter() - connect_started) * 1000, 1),
            )
            await channel.send(
                _start_payload(
                    task_id=task_id,
                    app_key=self._app_key,
                    text=text,
                    voice=vendor_voice,
                    audio_format=vendor_format,
                )
            )
            async for chunk in _consume_audio(channel, task_id=task_id):
                yield chunk

    def _build_ws_url(self, token: str) -> str:
        sep = "&" if "?" in self._ws_url else "?"
        return f"{self._ws_url}{sep}token={token}"


async def _consume_audio(
    channel: _WSChannel,
    *,
    task_id: str,
) -> AsyncIterator[TTSAudioChunk]:
    """Read frames from the WS, yielding audio chunks until completion.

    Binary frames are audio payloads. Text frames are control events:
    `SynthesisCompleted` ends the stream (we emit a terminal empty
    `is_final=True` chunk so the contract holds even if the last audio
    arrived in a prior frame), `TaskFailed` raises, everything else is
    control-plane noise that's dropped.
    """
    while True:
        try:
            raw = await channel.recv()
        except ConnectionClosedError as exc:
            raise TTSUpstreamError(
                f"aliyun tts connection closed mid-stream: {exc}",
                provider=PROVIDER_NAME,
            ) from exc
        except ConnectionClosed:
            # Clean close before SynthesisCompleted — emit the terminal
            # chunk so the iteration contract (exactly one is_final) holds.
            yield TTSAudioChunk(audio=b"", is_final=True)
            return

        if isinstance(raw, (bytes, bytearray)):
            yield TTSAudioChunk(audio=bytes(raw), is_final=False)
            continue

        event = _parse_event(raw, task_id=task_id)
        if event is None:
            continue
        name, payload = event
        if name == _EVT_COMPLETED:
            yield TTSAudioChunk(audio=b"", is_final=True)
            return
        if name == _EVT_TASK_FAILED:
            header = payload.get("header", {})
            message = header.get("status_text", "task failed")
            status_code = header.get("status")
            raise TTSUpstreamError(
                f"aliyun tts task failed: {message}",
                provider=PROVIDER_NAME,
                status_code=int(status_code) if isinstance(status_code, int) else None,
            )
        # _EVT_STARTED / MetaInfo / unknown — control noise, drop.


def _parse_event(
    raw: str,
    *,
    task_id: str,
) -> tuple[str, dict[str, Any]] | None:
    """Lift one WS text frame into `(event_name, full_payload)`.

    Returns `None` for malformed frames so the consumer loop skips them
    rather than aborting the whole synthesis.
    """
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        logger.warning("aliyun_tts_non_json_frame", task_id=task_id, sample=raw[:80])
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


def _start_payload(
    *,
    task_id: str,
    app_key: str,
    text: str,
    voice: str,
    audio_format: str,
) -> str:
    return json.dumps(
        {
            "header": {
                "message_id": uuid.uuid4().hex,
                "task_id": task_id,
                "namespace": "SpeechSynthesizer",
                "name": "StartSynthesis",
                "appkey": app_key,
            },
            "payload": {
                "text": text,
                "voice": voice,
                "format": audio_format,
                "sample_rate": _AUDIO_SAMPLE_RATE,
                "volume": 50,
                "speech_rate": 0,
                "pitch_rate": 0,
            },
        }
    )


def _map_status_error(exc: InvalidStatus) -> Exception:
    """HTTP-status -> typed TTSError. Aliyun returns 401/403 for an
    expired or revoked token; everything else is upstream-flavoured."""
    status_code = exc.response.status_code
    if status_code in (401, 403):
        return TTSAuthError(
            f"aliyun tts auth failed (ws upgrade {status_code})",
            provider=PROVIDER_NAME,
        )
    return TTSUpstreamError(
        f"aliyun tts ws upgrade failed ({status_code})",
        provider=PROVIDER_NAME,
        status_code=status_code,
    )


__all__ = ["AliyunTTSProvider"]
