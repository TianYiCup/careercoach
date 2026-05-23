"""Microsoft Edge TTS adapter — streaming synthesis over WSS.

Primary TTS provider per PRD/foundation §3.4.2. Edge TTS is the
Bing-Speech endpoint Microsoft Edge browsers call into for Read-Aloud;
it's free, requires no API key, and exposes ≈ 30 high-quality Mandarin
voices. We pick `zh-CN-XiaoxiaoNeural` as the v1 K voice — a warm
female timbre that matches the §3.0.5 F "K 像真人朋友" target.

Wire protocol — implemented inline (no third-party SDK)
-------------------------------------------------------
The endpoint is a single WSS:

    wss://speech.platform.bing.com/consumer/speech/synthesize
        /readaloud/edge/v1?TrustedClientToken=<TOKEN>

After connect, the client sends two text frames:

    1. Speech.Config — declares the output format + metadata options
    2. SSML         — the actual text wrapped in a `<speak>` envelope

The server then streams binary audio frames. Each binary frame is
prefixed with a 2-byte big-endian header length, then ASCII headers
(CRLF-separated, `\r\n\r\n` terminated) carrying the path/content-type,
followed by the audio bytes. The final text frame with
`Path: turn.end` marks the synthesis done.

We deliberately keep the protocol minimal — no metadata frames are
parsed, no word/sentence boundaries are exposed — because the API
surface (`TTSProvider`) only promises audio bytes + a terminal flag.

Testability
-----------
`EdgeTTSProvider.__init__` accepts a `ws_factory` callable that
defaults to `websockets.connect`. Tests pass a stub yielding a fake
bidirectional channel so we don't need network access or live Bing
credentials in CI.
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from datetime import UTC, datetime
from typing import Protocol
from xml.sax.saxutils import escape as xml_escape

import structlog
from websockets import connect as ws_connect
from websockets.exceptions import (
    ConnectionClosed,
    InvalidStatus,
    WebSocketException,
)

from app.tts.errors import (
    TTSAuthError,
    TTSError,
    TTSMalformedResponseError,
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

PROVIDER_NAME = "edge"

# The "Trusted Client Token" Microsoft Edge ships with — public; the
# Edge TTS reverse-engineering community treats it as a stable handshake
# secret. Out-of-rotation tokens return 401 on connect, which our
# `InvalidStatus` mapping below turns into `TTSAuthError` so ops sees a
# typed signal.
_DEFAULT_TRUSTED_CLIENT_TOKEN = "6A5AA1D4EAFF4E9FB37E23D68491D6F4"  # noqa: S105 — public Edge handshake token, not a secret

# Voice catalog mapping the stable API-side voice id to the vendor
# voice name. `k-warm` is the only voice v1 ships; bumping this dict
# is the only place a new voice needs wiring.
_VOICE_CATALOG: dict[TTSVoice, str] = {
    "k-warm": "zh-CN-XiaoxiaoNeural",
}

# Vendor output-format identifiers per container the API exposes.
# Keeping the lookup explicit means a future format addition is a
# typed diff rather than a string concat at the call site.
_FORMAT_CATALOG: dict[TTSAudioFormat, str] = {
    "mp3": "audio-24khz-48kbitrate-mono-mp3",
    "ogg": "ogg-24khz-16bit-mono-opus",
    "wav": "raw-24khz-16bit-mono-pcm",
}

# Edge TTS framing — binary frames carry the 2-byte big-endian header
# length first; the header section terminates at the canonical CRLF
# pair. Anchored as constants so a parse change is a single-line edit.
_HEADER_LENGTH_PREFIX_BYTES = 2
_HEADER_BODY_SEPARATOR = b"\r\n\r\n"

# Text-frame path identifiers (Path: <name>). Edge TTS uses these on
# both `audio` (binary) and signalling (`turn.end`) frames; we ignore
# `audio.metadata` per the protocol-minimal stance above.
_PATH_TURN_END = "turn.end"
_PATH_AUDIO = "audio"


class _WSChannel(Protocol):
    """Subset of `websockets.WebSocketClientProtocol` we touch.

    Stated explicitly so the test stub doesn't have to inherit from
    the real type. Matches v13's async-iterator protocol.
    """

    async def send(self, message: bytes | str) -> None: ...
    async def recv(self) -> bytes | str: ...
    async def close(self, code: int = 1000, reason: str = "") -> None: ...


class _WSConnection(Protocol):
    """The awaitable returned by `websockets.connect`. We use it as an
    async context manager so the adapter owns close lifetime
    explicitly."""

    async def __aenter__(self) -> _WSChannel: ...
    async def __aexit__(self, *exc: object) -> None: ...


WSFactory = Callable[[str], Awaitable[_WSConnection]]
"""Factory: URL -> awaited connection. Default is `websockets.connect`."""


class EdgeTTSProvider:
    """Streaming TTS client for Microsoft Edge's Read-Aloud endpoint.

    Structurally implements `app.tts.provider.TTSProvider`. The
    constructor accepts overridable `endpoint` + `trusted_client_token`
    + `ws_factory` for tests; production wiring uses the defaults.
    """

    name = PROVIDER_NAME

    def __init__(
        self,
        *,
        endpoint: str = (
            "wss://speech.platform.bing.com/consumer/speech/synthesize/readaloud/edge/v1"
        ),
        trusted_client_token: str = _DEFAULT_TRUSTED_CLIENT_TOKEN,
        ws_factory: WSFactory | None = None,
    ) -> None:
        self._endpoint = endpoint
        self._token = trusted_client_token
        self._ws_factory: WSFactory = ws_factory if ws_factory is not None else ws_connect

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
            # catalog entry would otherwise silently send an empty SSML.
            raise TTSUpstreamError(
                f"unsupported voice for edge backend: {voice!r}",
                provider=self.name,
            )
        vendor_format = _FORMAT_CATALOG.get(audio_format, _FORMAT_CATALOG["mp3"])
        url = f"{self._endpoint}?TrustedClientToken={self._token}"

        request_id = uuid.uuid4().hex
        logger.info(
            "tts_edge_synthesize_started",
            voice=voice,
            vendor_voice=vendor_voice,
            audio_format=audio_format,
            char_count=len(text),
            request_id=request_id,
        )

        try:
            async with await self._ws_factory(url) as channel:  # type: ignore[arg-type]
                await channel.send(_speech_config_frame(vendor_format))
                await channel.send(
                    _ssml_frame(text=text, voice=vendor_voice, request_id=request_id)
                )
                async for chunk in _consume_stream(
                    channel=channel,
                    timeout=timeout,
                    provider_name=self.name,
                ):
                    yield chunk
        except InvalidStatus as exc:
            status_code = getattr(exc.response, "status_code", None)
            if status_code in (401, 403):
                raise TTSAuthError(
                    f"edge tts auth failed: status {status_code}",
                    provider=self.name,
                ) from exc
            raise TTSUpstreamError(
                f"edge tts upgrade failed: status {status_code}",
                provider=self.name,
                status_code=status_code,
            ) from exc
        except TimeoutError as exc:
            raise TTSTimeoutError(
                f"edge tts timed out after {timeout}s",
                provider=self.name,
            ) from exc
        except WebSocketException as exc:
            raise TTSUpstreamError(
                f"edge tts websocket error: {exc}",
                provider=self.name,
            ) from exc


def _speech_config_frame(vendor_format: str) -> str:
    """First text frame Edge expects — declares output format."""
    timestamp = _utc_now_iso()
    body = (
        '{"context":{"synthesis":{"audio":{"metadataoptions":'
        '{"sentenceBoundaryEnabled":"false","wordBoundaryEnabled":"false"},'
        f'"outputFormat":"{vendor_format}"' + "}}}}"
    )
    return (
        f"X-Timestamp:{timestamp}\r\n"
        "Content-Type:application/json; charset=utf-8\r\n"
        "Path:speech.config\r\n"
        "\r\n"
        f"{body}"
    )


def _ssml_frame(*, text: str, voice: str, request_id: str) -> str:
    """Second text frame — SSML envelope carrying the actual content.

    We XML-escape `text` so a hint containing `<`, `>`, or `&` doesn't
    break the SSML or open an SSML-injection path on the upstream
    parser. SSML doesn't run untrusted markup the way HTML does, but
    escaping keeps the wire format deterministic.
    """
    timestamp = _utc_now_iso()
    safe_text = xml_escape(text)
    ssml = (
        "<speak version='1.0' xmlns='http://www.w3.org/2001/10/synthesis' "
        "xml:lang='zh-CN'>"
        f"<voice name='{voice}'>"
        f"<prosody pitch='+0Hz' rate='+0%' volume='+0%'>{safe_text}</prosody>"
        "</voice></speak>"
    )
    return (
        f"X-RequestId:{request_id}\r\n"
        f"X-Timestamp:{timestamp}\r\n"
        "Content-Type:application/ssml+xml\r\n"
        "Path:ssml\r\n"
        "\r\n"
        f"{ssml}"
    )


async def _consume_stream(
    *,
    channel: _WSChannel,
    timeout: float,
    provider_name: str,
) -> AsyncIterator[TTSAudioChunk]:
    """Read frames until `Path: turn.end` or the timeout budget runs out.

    Binary frames → `TTSAudioChunk(audio=…, is_final=False)`.
    `turn.end` text frame → terminal `TTSAudioChunk(audio=b"", is_final=True)`.
    """
    saw_audio = False
    try:
        while True:
            frame = await asyncio.wait_for(channel.recv(), timeout)
            if isinstance(frame, bytes):
                audio = _extract_audio_payload(frame, provider_name=provider_name)
                if audio:
                    saw_audio = True
                    yield TTSAudioChunk(audio=audio, is_final=False)
                continue
            # Text frame — only `turn.end` is load-bearing.
            if _frame_path(frame) == _PATH_TURN_END:
                logger.info(
                    "tts_edge_synthesize_completed",
                    saw_audio=saw_audio,
                )
                yield TTSAudioChunk(audio=b"", is_final=True)
                return
    except TimeoutError:
        raise
    except ConnectionClosed as exc:
        # If the connection closes after we've yielded audio but before
        # `turn.end`, we still treat it as a successful synthesis —
        # Edge sometimes drops the closing frame on short hints. The
        # error path is reserved for "we never got bytes at all".
        if saw_audio:
            logger.warning(
                "tts_edge_connection_closed_after_audio",
                code=exc.code,
                reason=exc.reason,
            )
            yield TTSAudioChunk(audio=b"", is_final=True)
            return
        raise TTSUpstreamError(
            f"edge tts closed before audio: code {exc.code}",
            provider=provider_name,
        ) from exc


def _extract_audio_payload(frame: bytes, *, provider_name: str) -> bytes:
    """Strip the Edge TTS binary header and return the audio body.

    Layout:
        [2-byte BE header length][header bytes][audio bytes]

    Raises `TTSMalformedResponseError` when the header length is
    absurd, the header doesn't contain the canonical separator, or the
    `Path:` field isn't `audio`. Vendor SDK drift would surface here
    most loudly.
    """
    if len(frame) < _HEADER_LENGTH_PREFIX_BYTES:
        raise TTSMalformedResponseError(
            "edge tts binary frame shorter than 2-byte header prefix",
            provider=provider_name,
        )
    header_len = int.from_bytes(frame[:_HEADER_LENGTH_PREFIX_BYTES], "big")
    header_end = _HEADER_LENGTH_PREFIX_BYTES + header_len
    if header_end > len(frame):
        raise TTSMalformedResponseError(
            f"edge tts header length {header_len} exceeds frame size {len(frame)}",
            provider=provider_name,
        )
    header_bytes = frame[_HEADER_LENGTH_PREFIX_BYTES:header_end]
    audio_bytes = frame[header_end:]
    sep_idx = header_bytes.find(_HEADER_BODY_SEPARATOR)
    if sep_idx == -1:
        # Newer protocol versions might split header sections across
        # two separators; treat the whole header_bytes as header text
        # for path-lookup purposes.
        header_text = header_bytes.decode("ascii", errors="replace")
    else:
        header_text = header_bytes[:sep_idx].decode("ascii", errors="replace")

    path = _path_from_header_text(header_text)
    if path != _PATH_AUDIO:
        # Metadata frames live in this channel too (Path: audio.metadata)
        # — we ignore them silently rather than raising so a future
        # protocol expansion doesn't crash the pipeline.
        return b""
    return audio_bytes


def _frame_path(frame: str) -> str:
    """Return the `Path:` header value from an Edge TTS text frame.

    Text frames are HTTP-ish: lines like `Header: value\r\n`
    terminated by `\r\n\r\n` then a JSON body. We only care about Path
    here so a regex is overkill — splitlines + prefix match keeps the
    intent obvious.
    """
    return _path_from_header_text(frame.split("\r\n\r\n", 1)[0])


def _path_from_header_text(header_text: str) -> str:
    for line in header_text.split("\r\n"):
        if line.lower().startswith("path:"):
            return line.split(":", 1)[1].strip()
    return ""


def _utc_now_iso() -> str:
    """ISO-8601 UTC timestamp formatted the way Edge expects.

    Edge requires the trailing `Z`-suffix variant; `datetime.isoformat`
    on a tzaware UTC datetime yields `+00:00` so we swap it. Edge does
    not strictly validate the timestamp, but a malformed one has been
    observed to elicit slower responses, so we keep this canonical.
    """
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S.000Z")


__all__ = ["EdgeTTSProvider"]


def _placate_unused_imports() -> None:
    # `TTSError` is re-exported by `app.tts.errors` and consumed via
    # `isinstance` in callers; keeping the symbol bound here lets the
    # error-class ordering above stay explicit in this module's
    # docstring without ruff flagging an unused import.
    _ = TTSError
