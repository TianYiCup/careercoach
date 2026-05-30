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

# When the dummy is fed *real* microphone audio (raw PCM, not UTF-8
# text), decoding the bytes yields a flood of replacement/null chars
# that (a) isn't a usable transcript and (b) blows past the moderation
# length cap + makes the LLM hint prompt huge and slow. For local mic
# testing we substitute a short, coherent opponent line so the rest of
# the pipeline (moderation → coach hint → TTS) actually exercises.
_BINARY_PLACEHOLDER_TRANSCRIPT = "我觉得这个方案风险太大了，还是再稳一点吧"  # noqa: RUF001

# Fraction of undecodable chars above which the buffer is treated as
# binary audio rather than text. Clean UTF-8 text (the test inputs)
# decodes with zero replacement/null chars, so the round-trip path is
# untouched.
_BINARY_CHAR_RATIO = 0.1


def _looks_like_binary_audio(text: str) -> bool:
    if not text:
        return False
    undecodable = sum(1 for c in text if c == "�" or c == "\x00")
    return undecodable / len(text) > _BINARY_CHAR_RATIO


def _decode(buffer: bytearray) -> str:
    text = buffer.decode("utf-8", errors="replace")
    return _BINARY_PLACEHOLDER_TRANSCRIPT if _looks_like_binary_audio(text) else text


class DummyASRProvider:
    """In-process echo ASR. Returns UTF-8-decoded chunks as partials,
    plus a trailing final event with the full transcript.

    Real audio (raw PCM) doesn't decode to text, so a binary buffer is
    surfaced as a short canned line instead of replacement-char noise —
    see `_looks_like_binary_audio`. No network I/O; the `timeout`
    parameter is accepted for Protocol conformance and otherwise ignored.
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
            yield ASREvent(kind="partial", text=_decode(buffer))

        final_text = _decode(buffer)
        logger.info("asr_dummy_finalized", char_count=len(final_text))
        yield ASREvent(kind="final", text=final_text)
