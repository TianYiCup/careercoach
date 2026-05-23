"""`TTSProvider` Protocol + `TTSAudioChunk` envelope.

PRD US-B2 — Coach K whispers a hint into the user's earphones during a
copilot session. The WS bridge today emits text-only `hint_done`
events; this Protocol is the seam a follow-up PR uses to also ship
synthesized audio, plus the seam a `POST /v1/tts/synthesize` REST
endpoint binds to so B-end clients can call it directly.

Shape choice — async iterator of bytes
--------------------------------------
Both candidate vendors (Microsoft Edge TTS, Aliyun NLS streaming TTS)
stream audio bytes back as the synthesis progresses, so an
`AsyncIterator[TTSAudioChunk]` lets the WS bridge forward bytes to the
client as fast as they arrive instead of buffering an entire utterance
in memory first. The envelope (`TTSAudioChunk`) carries the audio bytes
plus a `is_final` flag so consumers can implement back-pressure or
client-side end-of-stream detection without parsing the container
format.

Implementations MUST
--------------------
  * raise from `app.tts.errors` on failure (never bubble httpx /
    websockets errors)
  * emit at least one chunk with `is_final=True` even when the audio
    payload is empty — keeps the iteration contract uniform
  * honour `timeout` end-to-end (connect + read + total)
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Literal, Protocol, runtime_checkable

# Default budget matches the LLM + ASR layers (8s end-to-end). Real
# streaming TTS vendors emit the first chunk in <300ms, so the budget
# mostly guards against connection failures.
DEFAULT_TIMEOUT_SECONDS = 8.0

# Audio container we ask the vendor to return. `mp3` is the lowest
# common denominator — every browser + every wxapp `<audio>` tag plays
# it, and both candidate vendors support it natively, so a single
# format default keeps the router consumer-agnostic.
TTSAudioFormat = Literal["mp3", "ogg", "wav"]
DEFAULT_AUDIO_FORMAT: TTSAudioFormat = "mp3"

# Stable voice identifier exposed at our API boundary. The factory maps
# it down to vendor-specific voice names so a client switching providers
# never sees a different `voice` value in the response. v1 ships one
# voice — Coach K's warm Mandarin female — picked to match the
# §3.0.5 F "K 像真人朋友" target.
TTSVoice = Literal["k-warm"]
DEFAULT_VOICE: TTSVoice = "k-warm"

# Hard cap on the synthesizable text length. PRD US-B2 hints are
# ≤ 40 chars; 200 leaves headroom for sandbox-driven coach lines that
# extend the same TTS surface, while preventing a misuse where someone
# pipes an entire transcript through `/v1/tts/synthesize`.
MAX_TEXT_CHARACTERS = 200


@dataclass(frozen=True)
class TTSAudioChunk:
    """One audio chunk emitted by a streaming TTS provider.

    `audio`
        Container-encoded bytes (mp3/ogg/wav per `TTSAudioFormat`). A
        chunk may be empty when the vendor signals end-of-stream via a
        control frame rather than a tail audio payload — consumers that
        write to a file should still emit before checking `is_final`.
    `is_final`
        True on the last chunk of the synthesis. Vendor adapters MUST
        flip this on exactly one chunk, even when the body is empty.
    """

    audio: bytes
    is_final: bool = False


@runtime_checkable
class TTSProvider(Protocol):
    """Streaming text-to-speech provider.

    `runtime_checkable` so the factory can `isinstance`-check in dev
    tooling and tests. Production code depends on this Protocol
    structurally.
    """

    name: str
    """Stable identifier surfaced in logs and Langfuse traces."""

    def synthesize(
        self,
        text: str,
        *,
        voice: TTSVoice = DEFAULT_VOICE,
        audio_format: TTSAudioFormat = DEFAULT_AUDIO_FORMAT,
        timeout: float = DEFAULT_TIMEOUT_SECONDS,
    ) -> AsyncIterator[TTSAudioChunk]:
        """Stream `TTSAudioChunk`s for `text`.

        Returns an async iterator (NOT an awaitable) so callers can
        `async for chunk in provider.synthesize(...)` directly. The
        first iteration may perform the network connect; adapters MUST
        NOT do I/O before iteration begins.
        """
        ...
