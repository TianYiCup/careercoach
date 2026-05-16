"""DummyASRProvider — UTF-8-bytes-in, text-out streaming ASR mock.

Useful for two things:
  1. Unit tests of any consumer of the `ASRProvider` Protocol.
  2. Dev runs without an ASR vendor configured — the WS bridge (a
     future PR) can pipe through this and the developer sees their
     own text round-tripped, instead of needing live audio + an
     Aliyun account just to see the pipeline work.

The contract is the same one a real vendor (Aliyun NLS, Tencent ASR)
exposes: emit one `partial` per chunk with the cumulative transcript,
then exactly one `final` when the stream ends. The accumulating
buffer is required for CJK correctness — a Chinese character is 3
bytes in UTF-8 and may straddle a chunk boundary; decoding each chunk
independently would produce replacement characters.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

import structlog

from app.asr.provider import (
    DEFAULT_TIMEOUT_SECONDS,
    ASREvent,
)

logger = structlog.get_logger(__name__)


class DummyASRProvider:
    """In-process echo ASR. Returns UTF-8-decoded chunks as partials,
    plus a trailing final event with the full transcript.

    No network I/O; the `timeout` parameter is accepted for Protocol
    conformance and otherwise ignored.
    """

    name = "dummy"

    async def transcribe_stream(
        self,
        audio_chunks: AsyncIterator[bytes],
        *,
        timeout: float = DEFAULT_TIMEOUT_SECONDS,
    ) -> AsyncIterator[ASREvent]:
        # Accumulate raw bytes; decode the full buffer each iteration
        # so a CJK character split across two chunks doesn't produce
        # a U+FFFD replacement char in the partial.
        buffer = bytearray()
        async for chunk in audio_chunks:
            buffer.extend(chunk)
            text = buffer.decode("utf-8", errors="replace")
            yield ASREvent(kind="partial", text=text)

        final_text = buffer.decode("utf-8", errors="replace")
        logger.info("asr_dummy_finalized", char_count=len(final_text))
        yield ASREvent(kind="final", text=final_text)
