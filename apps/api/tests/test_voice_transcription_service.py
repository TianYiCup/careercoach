"""Unit tests for `VoiceTranscriptionService` — the ASR step of the
voice turn (PRD §7.4)."""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest
from app.asr import ASREvent, ASRUpstreamError
from app.asr.dummy import DummyASRProvider
from app.asr.provider import DEFAULT_TIMEOUT_SECONDS
from app.services.sessions.voice import (
    VoiceTranscriptionError,
    VoiceTranscriptionService,
)


class _FailingASR:
    """ASRProvider that always raises — simulates a vendor outage."""

    name = "failing"

    async def transcribe_stream(
        self,
        audio_chunks: AsyncIterator[bytes],
        *,
        timeout: float = DEFAULT_TIMEOUT_SECONDS,
    ) -> AsyncIterator[ASREvent]:
        _ = (audio_chunks, timeout)
        raise ASRUpstreamError("simulated ASR outage", provider="failing")
        yield ASREvent(kind="final", text="")  # pragma: no cover — makes this a generator


async def test_transcribe_returns_final_transcript() -> None:
    """DummyASRProvider echoes UTF-8 bytes — the blob round-trips as its
    own transcript."""
    service = VoiceTranscriptionService(asr=DummyASRProvider())
    transcript = await service.transcribe("赵总，我周末有重要安排".encode())
    assert transcript == "赵总，我周末有重要安排"


async def test_transcribe_strips_surrounding_whitespace() -> None:
    service = VoiceTranscriptionService(asr=DummyASRProvider())
    assert await service.transcribe("  你好  ".encode()) == "你好"


async def test_transcribe_empty_audio_yields_empty_string() -> None:
    """An empty / silent utterance comes back as `""` — the route turns
    that into the PRD US-A3 '没听清' response."""
    service = VoiceTranscriptionService(asr=DummyASRProvider())
    assert await service.transcribe(b"") == ""


async def test_transcribe_wraps_asr_error() -> None:
    """Any `ASRError` from the provider surfaces as the layer's own
    `VoiceTranscriptionError`; vendor errors never bubble through."""
    service = VoiceTranscriptionService(asr=_FailingASR())
    with pytest.raises(VoiceTranscriptionError):
        await service.transcribe(b"whatever")
