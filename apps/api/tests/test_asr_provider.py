"""Unit tests for the `ASRProvider` Protocol surface + `DummyASRProvider`.

A-17 ships the ASR adapter abstraction so a future PR can wire it into
the copilot WS loop. This PR does NOT modify the WS endpoint — we just
build the seam (Protocol + dummy impl + factory) and pin its contract
with tests so the integration is mechanical.

The Protocol shape mirrors the LLM layer's `stream_chat`: an async
iterator of typed events that yields partial+final transcripts. The
dummy impl decodes UTF-8 bytes 1:1 — useful for tests, useful for the
WS bridge before a real ASR vendor (Aliyun NLS / Tencent ASR) lands.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest
from app.asr import (
    ASREvent,
    ASRProvider,
    DummyASRProvider,
)
from app.asr.dummy import _BINARY_PLACEHOLDER_TRANSCRIPT


async def _chunks(*pieces: bytes) -> AsyncIterator[bytes]:
    for piece in pieces:
        yield piece


# --------------------------------------------------------------------- #
# DummyASRProvider — happy path                                          #
# --------------------------------------------------------------------- #


async def test_dummy_emits_one_partial_per_chunk_then_final() -> None:
    """The dummy is supposed to behave like a real streaming ASR:
    one partial event per chunk in (accumulating text), then exactly
    one final event with the full transcript when the stream ends.
    Real vendors emit the same shape — the dummy is contract-faithful
    so the future WS bridge can be written against this shape and
    swap in Aliyun without code changes."""
    provider = DummyASRProvider()
    audio = _chunks(b"hello ", b"world")

    events = [event async for event in provider.transcribe_stream(audio)]

    assert events == [
        ASREvent(kind="partial", text="hello "),
        ASREvent(kind="partial", text="hello world"),
        ASREvent(kind="final", text="hello world"),
    ]


async def test_dummy_handles_empty_stream() -> None:
    """Zero audio chunks → no partials, but still exactly one final
    (with empty text). The final-on-stream-end contract is what tells
    the WS bridge "this utterance is done, hand it to the coach".
    Skipping the final on an empty stream would force the bridge to
    distinguish "stream ended cleanly" from "no events at all"."""
    provider = DummyASRProvider()

    events = [event async for event in provider.transcribe_stream(_chunks())]

    assert events == [ASREvent(kind="final", text="")]


async def test_dummy_handles_single_chunk() -> None:
    provider = DummyASRProvider()
    audio = _chunks(b"hi")

    events = [event async for event in provider.transcribe_stream(audio)]

    assert events == [
        ASREvent(kind="partial", text="hi"),
        ASREvent(kind="final", text="hi"),
    ]


async def test_dummy_preserves_cjk_text_across_chunk_boundaries() -> None:
    """Chinese characters are 3 bytes each in UTF-8; the dummy must
    not split a character across a chunk boundary into mojibake. The
    accumulating-buffer-then-decode pattern is the only way this works
    — if we decoded each chunk independently, splitting "教" between
    two chunks would produce two replacement characters."""
    provider = DummyASRProvider()
    full = "教练 K".encode()
    # Split mid-character: "教" is 3 bytes, send byte 1 then bytes 2..end.
    chunks = _chunks(full[:1], full[1:])

    events = [event async for event in provider.transcribe_stream(chunks)]

    # Final must be the round-tripped string — no replacement chars.
    assert events[-1] == ASREvent(kind="final", text="教练 K")


async def test_dummy_substitutes_canned_line_for_binary_audio() -> None:
    """Fed real PCM (not UTF-8 text), the dummy must NOT surface a flood
    of replacement/null chars — that breaks the moderation length cap
    and makes the coach-hint prompt huge. It returns a short canned
    opponent line so the rest of the pipeline still exercises locally."""
    provider = DummyASRProvider()
    # PCM-ish: alternating high bytes + nulls that don't decode to text.
    pcm = bytes([0x00, 0x00, 0xC8, 0x01, 0x00, 0x00, 0x96, 0xFF]) * 200
    audio = _chunks(pcm)

    events = [event async for event in provider.transcribe_stream(audio)]

    assert events[-1] == ASREvent(kind="final", text=_BINARY_PLACEHOLDER_TRANSCRIPT)


# --------------------------------------------------------------------- #
# DummyASRProvider — protocol conformance                                #
# --------------------------------------------------------------------- #


def test_dummy_satisfies_provider_protocol() -> None:
    """`runtime_checkable` Protocol means `isinstance` works structurally.
    Pin this so the factory layer can rely on it for dev tooling."""
    provider = DummyASRProvider()
    assert isinstance(provider, ASRProvider)


def test_dummy_name_is_stable() -> None:
    """The `name` field is used in logs and (later) Langfuse traces;
    pin the literal value so dashboards can hardcode the filter."""
    assert DummyASRProvider().name == "dummy"


# --------------------------------------------------------------------- #
# ASREvent immutability                                                  #
# --------------------------------------------------------------------- #


def test_asr_event_is_frozen() -> None:
    """Frozen dataclass so callers can hand the same event to multiple
    sinks (the WS send + a Langfuse trace span, say) without one
    mutating the other."""
    event = ASREvent(kind="partial", text="hi")

    with pytest.raises(Exception):  # noqa: B017 — dataclasses raise FrozenInstanceError
        event.text = "boom"  # type: ignore[misc]


def test_asr_event_confidence_defaults_to_none() -> None:
    """The dummy never reports confidence; pin that the field defaults
    to None so the type checker stays happy when the bridge checks
    `if event.confidence is not None`."""
    event = ASREvent(kind="final", text="ok")
    assert event.confidence is None
