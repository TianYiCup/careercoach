"""`ASRProvider` Protocol + `ASREvent` envelope.

A-17 introduces the ASR seam. The Protocol shape mirrors
`app.llm.provider.LLMProvider`: an async iterator of typed events so
adapters can stream partials as fast as the vendor emits them, then
the future WS bridge forwards them through the same envelope
discriminator the copilot stream uses.

Why we picked `transcribe_stream(audio_chunks)` over `transcribe_text`
---------------------------------------------------------------------
Real ASR vendors (Aliyun NLS, Tencent ASR, Whisper streaming) are all
audio-in, text-out, partial-then-final. Designing the Protocol around
their native shape means the dummy is the only adapter that does any
work to fake it — real adapters are a thin wire-format shim.

The `privacy_level=high` path (US-B3) does on-device ASR client-side
and bypasses this Protocol entirely; the WS bridge will detect that
case and skip the ASR call rather than route through a degenerate
"echo-this-text" adapter.

Implementations MUST
--------------------
  * raise from `app.asr.errors` on failure (never bubble httpx errors)
  * emit exactly one `kind="final"` event when the stream ends
  * honour `timeout` end-to-end (connect + read + total)
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Literal, Protocol, runtime_checkable

# Default budget matches the LLM layer (8s end-to-end). Real streaming
# ASR vendors return first partial well under 1s; the budget mostly
# guards against connection failures.
DEFAULT_TIMEOUT_SECONDS = 8.0

ASREventKind = Literal["partial", "final"]


@dataclass(frozen=True)
class ASREvent:
    """One transcript event from a streaming ASR provider.

    `kind`
        `"partial"` — interim transcript, may be revised.
        `"final"`   — terminal transcript for this utterance.
    `text`
        Cumulative transcript so far. Final event's text is the
        canonical transcript for the utterance.
    `confidence`
        0..1 float when the vendor reports it; None when unknown.
        Frozen so callers can hand the same event to multiple sinks
        (WS send + Langfuse trace span, say) without one mutating
        the other.
    """

    kind: ASREventKind
    text: str
    confidence: float | None = None


@runtime_checkable
class ASRProvider(Protocol):
    """Streaming speech-to-text provider.

    `runtime_checkable` so the factory can `isinstance`-check in tests
    and dev tooling. Production code should depend on the Protocol
    structurally, not via isinstance.
    """

    name: str
    """Stable identifier used in logs and (later) Langfuse traces."""

    def transcribe_stream(
        self,
        audio_chunks: AsyncIterator[bytes],
        *,
        timeout: float = DEFAULT_TIMEOUT_SECONDS,
    ) -> AsyncIterator[ASREvent]:
        """Stream partial+final transcript events.

        Returns an async iterator (NOT an awaitable) so callers can
        `async for event in provider.transcribe_stream(...)` directly.
        The first iteration may perform the network connect; adapters
        should NOT do I/O before iteration begins.
        """
        ...
