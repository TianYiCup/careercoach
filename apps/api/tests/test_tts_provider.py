"""Unit tests for the `TTSProvider` Protocol surface + `DummyTTSProvider`.

Pin the contract a real adapter (`EdgeTTSProvider`, `AliyunTTSProvider`)
must follow: an async iterator of `TTSAudioChunk` ending in exactly one
chunk with `is_final=True`. The dummy must emit a playable WAV blob
sized roughly to the input length so downstream UI tests can assert on
audio duration metadata.
"""

from __future__ import annotations

import pytest
from app.tts import (
    DummyTTSProvider,
    TTSAudioChunk,
    TTSProvider,
)

# --------------------------------------------------------------------- #
# DummyTTSProvider — happy path                                          #
# --------------------------------------------------------------------- #


async def test_dummy_emits_one_audio_then_one_final() -> None:
    """The dummy contract: at least one audio chunk, then exactly one
    final chunk. The terminal chunk's audio payload may be empty —
    consumers check `is_final`, not the byte length."""
    provider = DummyTTSProvider()

    chunks = [chunk async for chunk in provider.synthesize("你好 K", audio_format="wav")]

    assert len(chunks) == 2
    assert chunks[0].is_final is False
    assert chunks[1].is_final is True
    assert chunks[0].audio  # non-empty audio
    assert chunks[1].audio == b""


async def test_dummy_audio_is_valid_riff_wave() -> None:
    """The dummy's first chunk must start with a RIFF/WAVE header so a
    client `<audio>` tag can play it without container negotiation."""
    provider = DummyTTSProvider()

    chunks = [c async for c in provider.synthesize("hi", audio_format="wav")]
    payload = chunks[0].audio

    # RIFF header layout: "RIFF"(4) <size>(4) "WAVE"(4) "fmt "(4) ...
    assert payload.startswith(b"RIFF")
    assert payload[8:12] == b"WAVE"
    assert payload[12:16] == b"fmt "


async def test_dummy_audio_size_scales_with_text_length() -> None:
    """Longer input → bigger audio payload. Lets UI tests assert that
    K's whisper duration grows with hint length."""
    provider = DummyTTSProvider()

    short_chunks = [c async for c in provider.synthesize("a", audio_format="wav")]
    long_chunks = [c async for c in provider.synthesize("a" * 50, audio_format="wav")]

    assert len(long_chunks[0].audio) > len(short_chunks[0].audio)


async def test_dummy_handles_short_text_with_min_duration() -> None:
    """Even one character produces a playable file — the min-duration
    floor in `_MIN_DURATION_SECONDS` prevents a zero-sample WAV that
    some players reject."""
    provider = DummyTTSProvider()

    chunks = [c async for c in provider.synthesize("a", audio_format="wav")]

    # 0.1s × 16kHz = 1600 samples × 2 bytes = 3200 bytes audio + 44 header.
    assert len(chunks[0].audio) >= 44 + 1600 * 2


async def test_dummy_logs_when_audio_format_mismatches() -> None:
    """Asking the dummy for mp3 still gets WAV — but the mismatch must
    be observable so a test pipeline catches a wrong-format assumption.

    We don't assert on the log content (structlog routing is
    environment-specific) — we just exercise the path to make sure the
    early return path doesn't suppress the chunks themselves.
    """
    provider = DummyTTSProvider()

    chunks = [c async for c in provider.synthesize("hi", audio_format="mp3")]

    assert chunks[0].audio  # served regardless of format mismatch
    assert chunks[-1].is_final


# --------------------------------------------------------------------- #
# DummyTTSProvider — protocol conformance                                #
# --------------------------------------------------------------------- #


def test_dummy_satisfies_provider_protocol() -> None:
    """`runtime_checkable` Protocol means `isinstance` works
    structurally. Pin so the factory can rely on it for dev tooling."""
    provider = DummyTTSProvider()
    assert isinstance(provider, TTSProvider)


def test_dummy_name_is_stable() -> None:
    """The `name` field is surfaced in logs + Langfuse traces; pin the
    literal value so dashboards can hardcode the filter."""
    assert DummyTTSProvider().name == "dummy"


# --------------------------------------------------------------------- #
# TTSAudioChunk immutability                                             #
# --------------------------------------------------------------------- #


def test_audio_chunk_is_frozen() -> None:
    """Frozen so a consumer can hand the same chunk to multiple sinks
    (WS send + Langfuse trace, say) without one mutating the other."""
    chunk = TTSAudioChunk(audio=b"x", is_final=False)

    with pytest.raises(Exception):  # noqa: B017 — dataclasses raise FrozenInstanceError
        chunk.audio = b"y"  # type: ignore[misc]


def test_audio_chunk_is_final_defaults_to_false() -> None:
    """Default `is_final=False` matches the common case (intermediate
    audio frames). Consumers that emit only one chunk must pass
    `is_final=True` explicitly."""
    chunk = TTSAudioChunk(audio=b"x")
    assert chunk.is_final is False
