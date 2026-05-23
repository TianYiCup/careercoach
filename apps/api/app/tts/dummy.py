"""DummyTTSProvider — synthetic WAV payload, no network.

Useful for two things:
  1. Unit tests of any consumer of the `TTSProvider` Protocol.
  2. Dev runs without a TTS vendor configured — the WS bridge can pipe
     through this and the developer hears a short audible blip per
     hint, instead of needing a live Edge / Aliyun account just to see
     the pipeline work end-to-end.

The contract matches what a real vendor (Edge TTS WSS, Aliyun NLS TTS)
exposes: emit one or more chunks with audio bytes, then exactly one
chunk with `is_final=True`. The audio payload is a minimal RIFF/WAVE
header followed by silence sized roughly proportional to the text
length so the client's `<audio>` tag reports a non-zero duration —
makes the dummy useful for end-to-end UI tests where the client
asserts on playback metadata rather than the audio content itself.
"""

from __future__ import annotations

import math
import struct
from collections.abc import AsyncIterator

import structlog

from app.tts.provider import (
    DEFAULT_AUDIO_FORMAT,
    DEFAULT_TIMEOUT_SECONDS,
    DEFAULT_VOICE,
    TTSAudioChunk,
    TTSAudioFormat,
    TTSVoice,
)

logger = structlog.get_logger(__name__)

# 16kHz mono 16-bit PCM — same sample rate the ASR side standardises
# on, so reusing this dummy across surfaces stays cohesive.
_SAMPLE_RATE = 16000
_BITS_PER_SAMPLE = 16
_NUM_CHANNELS = 1

# Roughly one second of silence per ten characters keeps the perceived
# tempo of "K speaking" lined up with the visible text without a real
# speech engine. 0.1s minimum so a one-character hint still yields a
# playable file.
_SECONDS_PER_CHAR = 0.10
_MIN_DURATION_SECONDS = 0.10


class DummyTTSProvider:
    """In-process WAV synthesizer. Returns one chunk per call with the
    full audio payload, then a terminal `is_final=True` empty chunk.

    No network I/O; `timeout` is accepted for Protocol conformance and
    otherwise ignored. The `audio_format` parameter is accepted but
    always served as WAV — `format` mismatch is logged so a downstream
    test catches a wrong-format pipeline assumption.
    """

    name = "dummy"

    async def synthesize(
        self,
        text: str,
        *,
        voice: TTSVoice = DEFAULT_VOICE,
        audio_format: TTSAudioFormat = DEFAULT_AUDIO_FORMAT,
        timeout: float = DEFAULT_TIMEOUT_SECONDS,
    ) -> AsyncIterator[TTSAudioChunk]:
        duration_s = max(_MIN_DURATION_SECONDS, len(text) * _SECONDS_PER_CHAR)
        sample_count = math.ceil(duration_s * _SAMPLE_RATE)
        audio = _wav_silence_bytes(sample_count)

        if audio_format != "wav":
            # Surface mismatch loudly — a real dev who wired the dummy
            # into a player expecting mp3 will see this in logs.
            logger.info(
                "tts_dummy_format_mismatch",
                requested=audio_format,
                served="wav",
                char_count=len(text),
            )

        logger.info(
            "tts_dummy_synthesized",
            char_count=len(text),
            sample_count=sample_count,
            voice=voice,
        )
        yield TTSAudioChunk(audio=audio, is_final=False)
        yield TTSAudioChunk(audio=b"", is_final=True)


def _wav_silence_bytes(sample_count: int) -> bytes:
    """Build a minimal RIFF/WAVE blob containing `sample_count` PCM-16
    zero samples (silence).

    The header layout (44 bytes) is the standard 16-bit-mono PCM WAV
    — keeping it spelled out instead of pulling in `wave` from the
    stdlib avoids a `BytesIO` round-trip and the dependency reads
    naturally next to the constants above.
    """
    byte_rate = _SAMPLE_RATE * _NUM_CHANNELS * _BITS_PER_SAMPLE // 8
    block_align = _NUM_CHANNELS * _BITS_PER_SAMPLE // 8
    data_size = sample_count * block_align
    riff_size = 36 + data_size  # full file size minus the "RIFF" + size fields

    header = struct.pack(
        "<4sI4s4sIHHIIHH4sI",
        b"RIFF",
        riff_size,
        b"WAVE",
        b"fmt ",
        16,  # fmt chunk size
        1,  # audio format = PCM
        _NUM_CHANNELS,
        _SAMPLE_RATE,
        byte_rate,
        block_align,
        _BITS_PER_SAMPLE,
        b"data",
        data_size,
    )
    return header + b"\x00" * data_size
