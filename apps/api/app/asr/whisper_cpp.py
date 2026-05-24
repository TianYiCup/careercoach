"""whisper.cpp self-hosted HTTP ASR adapter.

Closes the second half of phase-review §三-③: the "端侧 whisper.cpp"
half of US-B3 隐私模式. The PRD's original framing called for the
model to run on the user's device; that's structurally a B-end concern
(Tauri Rust / Taro miniapp can't be touched per A/B division). This
adapter ships the server-side complement — a self-hosted whisper.cpp
HTTP server, deployable on an air-gapped box, that lets ops route
audio through a non-Aliyun path when the user opts into privacy mode.

What this is and is not
-----------------------
* IS: a thin HTTP shim over `whisper-server` (the CLI binary that
      ships with whisper.cpp; exposes `POST /inference` with a
      multipart audio body and a JSON `{text: ...}` response).
* IS NOT: an on-device synthesizer. Real privacy-mode-on-the-phone
      stays a B-end deliverable; this adapter just gives ops a
      self-hosted alternative to the cloud vendor.

Why a "thin" implementation despite the streaming ASRProvider contract
----------------------------------------------------------------------
whisper.cpp's HTTP server is non-streaming: you POST the full
utterance audio, you get the full transcript back. We collect the
incoming `audio_chunks` iterator into a buffer, ship one HTTP request,
and emit exactly one `ASREvent(kind="final")` — the contract allows
zero partials. The WS bridge already handles "received only a final,
no partials" cleanly (the moderation + hint path triggers on final
text).

Testability
-----------
`WhisperCppASRProvider.__init__` accepts an `http_client_factory`
callable that defaults to building an `httpx.AsyncClient`. Tests
inject a stub yielding a pre-canned response object so we don't need
a real whisper-server in CI.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable
from typing import Any

import httpx
import structlog

from app.asr.errors import (
    ASRAuthError,
    ASRMalformedResponseError,
    ASRTimeoutError,
    ASRUpstreamError,
)
from app.asr.provider import DEFAULT_TIMEOUT_SECONDS, ASREvent

logger = structlog.get_logger(__name__)

PROVIDER_NAME = "whisper_cpp"

# whisper-server's REST endpoint. The path is hardcoded in upstream
# (`examples/server/server.cpp`), so we anchor it as a constant rather
# than wiring it through settings — a future drift would be a one-line
# change here, not a config churn.
_INFERENCE_PATH = "/inference"

# Multipart field names whisper-server expects. The audio field MUST
# be named `file`; other fields (response_format, temperature, etc.)
# are optional and we keep them off the call so the server defaults
# (json output, T=0) apply.
_AUDIO_FIELD_NAME = "file"
_AUDIO_FILENAME = "utterance.wav"
_AUDIO_CONTENT_TYPE = "audio/wav"


HttpClientFactory = Callable[[], httpx.AsyncClient]
"""Builder for the `httpx.AsyncClient` used per call.

Per-call construction (vs a long-lived client) keeps tests isolated
— each provider call exits its own context manager so a test stub
can assert on `__aenter__` / `__aexit__` without sharing state across
tests. The cost (TCP handshake per call) is negligible for an
in-process self-hosted server.
"""


class WhisperCppASRProvider:
    """Self-hosted whisper.cpp HTTP ASR client.

    Structurally implements `app.asr.provider.ASRProvider`. Buffers
    incoming audio chunks, ships one POST per utterance, emits a
    single `kind="final"` event with the transcript.
    """

    name = PROVIDER_NAME

    def __init__(
        self,
        *,
        base_url: str,
        http_client_factory: HttpClientFactory | None = None,
    ) -> None:
        if not base_url:
            raise ValueError("whisper_cpp ASR: base_url must be set")
        # Strip a trailing slash so the joined URL never doubles up.
        self._base_url = base_url.rstrip("/")
        self._http_client_factory: HttpClientFactory = (
            http_client_factory if http_client_factory is not None else httpx.AsyncClient
        )

    async def transcribe_stream(
        self,
        audio_chunks: AsyncIterator[bytes],
        *,
        timeout: float = DEFAULT_TIMEOUT_SECONDS,
    ) -> AsyncIterator[ASREvent]:
        """Buffer the utterance, transcribe via HTTP, yield one final event.

        Empty utterance (zero chunks or all-empty chunks) short-circuits
        with `ASREvent(kind="final", text="")` — same shape the dummy
        adapter exposes for empty streams, so the WS bridge's
        skip-when-empty logic in copilot.py applies without a special
        case for this backend.
        """
        buffer = bytearray()
        async for chunk in audio_chunks:
            if chunk:
                buffer.extend(chunk)

        if not buffer:
            logger.info("asr_whisper_cpp_empty_utterance")
            yield ASREvent(kind="final", text="")
            return

        url = f"{self._base_url}{_INFERENCE_PATH}"
        files = {
            _AUDIO_FIELD_NAME: (
                _AUDIO_FILENAME,
                bytes(buffer),
                _AUDIO_CONTENT_TYPE,
            ),
        }

        try:
            async with self._http_client_factory() as client:
                response = await client.post(url, files=files, timeout=timeout)
        except httpx.TimeoutException as exc:
            raise ASRTimeoutError(
                f"whisper.cpp transcribe exceeded {timeout}s",
                provider=PROVIDER_NAME,
            ) from exc
        except httpx.HTTPError as exc:
            raise ASRUpstreamError(
                f"whisper.cpp transport error: {exc}",
                provider=PROVIDER_NAME,
            ) from exc

        if response.status_code in (401, 403):
            raise ASRAuthError(
                f"whisper.cpp rejected the request: status {response.status_code}",
                provider=PROVIDER_NAME,
            )
        if response.status_code >= 400:
            raise ASRUpstreamError(
                f"whisper.cpp returned {response.status_code}",
                provider=PROVIDER_NAME,
                status_code=response.status_code,
            )

        text = _extract_transcript_text(response, provider=PROVIDER_NAME)
        logger.info(
            "asr_whisper_cpp_transcribed",
            audio_bytes=len(buffer),
            char_count=len(text),
        )
        yield ASREvent(kind="final", text=text)


def _extract_transcript_text(response: httpx.Response, *, provider: str) -> str:
    """Decode whisper-server's JSON body and return the transcript text.

    The canonical response shape is `{"text": "..."}`. Some
    whisper.cpp builds return a `segments` array alongside the top
    level text; we ignore that — the cumulative `text` field is the
    authoritative transcript.

    Raises `ASRMalformedResponseError` when the body isn't JSON or
    doesn't carry a string `text` field — distinct from a network /
    upstream-status failure so analysts can split parse drift from
    real server outages.
    """
    try:
        body: Any = response.json()
    except ValueError as exc:
        raise ASRMalformedResponseError(
            "whisper.cpp returned non-JSON body",
            provider=provider,
        ) from exc

    if not isinstance(body, dict) or "text" not in body:
        raise ASRMalformedResponseError(
            "whisper.cpp response missing `text` field",
            provider=provider,
        )

    text = body["text"]
    if not isinstance(text, str):
        raise ASRMalformedResponseError(
            f"whisper.cpp `text` field is {type(text).__name__}, expected str",
            provider=provider,
        )
    # whisper.cpp tends to prepend a leading space on the transcript
    # (artifact of the tokenizer's word-boundary handling). Strip it
    # so downstream length checks + log fields stay clean.
    return text.strip()


__all__ = ["WhisperCppASRProvider"]
