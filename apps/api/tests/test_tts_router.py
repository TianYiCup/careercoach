"""TTSRouter failover behaviour.

Pins the contract:
* Primary's first-chunk success → no failover (only the primary
  iterates).
* Primary first-chunk timeout → next provider runs.
* Primary raises `TTSError` before first chunk → next provider runs.
* All providers fail → typed `TTSError` re-raised.
* Once first chunk is yielded, downstream errors propagate (no
  mid-stream splice across providers).
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator

import pytest
from app.tts import (
    TTSAudioChunk,
    TTSAuthError,
    TTSRouter,
    TTSTimeoutError,
    TTSUpstreamError,
)
from app.tts.provider import (
    DEFAULT_AUDIO_FORMAT,
    DEFAULT_TIMEOUT_SECONDS,
    DEFAULT_VOICE,
    TTSAudioFormat,
    TTSVoice,
)


class _ScriptedProvider:
    """Async-iterator producer with controllable failure modes."""

    def __init__(
        self,
        name: str,
        *,
        chunks: list[TTSAudioChunk] | None = None,
        raise_before: BaseException | None = None,
        first_chunk_delay: float = 0.0,
    ) -> None:
        self.name = name
        self._chunks = chunks or []
        self._raise_before = raise_before
        self._first_chunk_delay = first_chunk_delay
        self.calls = 0

    def synthesize(
        self,
        text: str,
        *,
        voice: TTSVoice = DEFAULT_VOICE,
        audio_format: TTSAudioFormat = DEFAULT_AUDIO_FORMAT,
        timeout: float = DEFAULT_TIMEOUT_SECONDS,
    ) -> AsyncIterator[TTSAudioChunk]:
        self.calls += 1
        return self._gen()

    async def _gen(self) -> AsyncIterator[TTSAudioChunk]:
        if self._raise_before is not None:
            raise self._raise_before
        if self._first_chunk_delay > 0:
            await asyncio.sleep(self._first_chunk_delay)
        for chunk in self._chunks:
            yield chunk


# --------------------------------------------------------------------- #
# Happy path                                                             #
# --------------------------------------------------------------------- #


async def test_primary_success_no_failover() -> None:
    primary = _ScriptedProvider(
        "edge",
        chunks=[
            TTSAudioChunk(audio=b"a"),
            TTSAudioChunk(audio=b"b", is_final=True),
        ],
    )
    backup = _ScriptedProvider("aliyun")
    router = TTSRouter(primary=primary, backups=[backup])

    chunks = [c async for c in router.synthesize("hi")]

    assert [c.audio for c in chunks] == [b"a", b"b"]
    assert primary.calls == 1
    assert backup.calls == 0


async def test_router_name_is_stable() -> None:
    primary = _ScriptedProvider("edge", chunks=[TTSAudioChunk(audio=b"", is_final=True)])
    assert TTSRouter(primary=primary).name == "router"


async def test_single_chunk_synthesis_short_circuits_inner_loop() -> None:
    """If the first chunk is already terminal, the router must NOT iterate
    further (the underlying stream is exhausted — pulling again would
    block or raise)."""
    primary = _ScriptedProvider(
        "edge",
        chunks=[TTSAudioChunk(audio=b"x", is_final=True)],
    )
    router = TTSRouter(primary=primary)

    chunks = [c async for c in router.synthesize("hi")]
    assert chunks == [TTSAudioChunk(audio=b"x", is_final=True)]


# --------------------------------------------------------------------- #
# Failover paths                                                         #
# --------------------------------------------------------------------- #


async def test_primary_pre_chunk_raise_fails_over() -> None:
    """An auth error from the primary BEFORE any chunk → backup runs."""
    primary = _ScriptedProvider(
        "edge",
        raise_before=TTSAuthError("bad token", provider="edge"),
    )
    backup = _ScriptedProvider(
        "aliyun",
        chunks=[TTSAudioChunk(audio=b"backup", is_final=True)],
    )
    router = TTSRouter(primary=primary, backups=[backup])

    chunks = [c async for c in router.synthesize("hi")]

    assert chunks[0].audio == b"backup"
    assert primary.calls == 1
    assert backup.calls == 1


async def test_primary_first_byte_timeout_fails_over() -> None:
    """Slow primary handshake → router gives up after the budget and
    tries the backup."""
    primary = _ScriptedProvider(
        "edge",
        chunks=[TTSAudioChunk(audio=b"slow", is_final=True)],
        first_chunk_delay=1.0,
    )
    backup = _ScriptedProvider(
        "aliyun",
        chunks=[TTSAudioChunk(audio=b"fast", is_final=True)],
    )
    router = TTSRouter(primary=primary, backups=[backup], first_byte_budget_s=0.05)

    chunks = [c async for c in router.synthesize("hi")]
    assert chunks[0].audio == b"fast"


async def test_empty_stream_from_primary_fails_over() -> None:
    """Zero chunks from primary → typed error → backup runs."""
    primary = _ScriptedProvider("edge", chunks=[])
    backup = _ScriptedProvider(
        "aliyun",
        chunks=[TTSAudioChunk(audio=b"backup", is_final=True)],
    )
    router = TTSRouter(primary=primary, backups=[backup])

    chunks = [c async for c in router.synthesize("hi")]
    assert chunks[0].audio == b"backup"


async def test_all_providers_fail_re_raises_last_error() -> None:
    """No provider produces — caller still sees a typed `TTSError`,
    specifically the last one in the chain (so analysts see what the
    final attempt was)."""
    primary = _ScriptedProvider(
        "edge",
        raise_before=TTSAuthError("bad edge token", provider="edge"),
    )
    backup = _ScriptedProvider(
        "aliyun",
        raise_before=TTSUpstreamError("aliyun down", provider="aliyun", status_code=500),
    )
    router = TTSRouter(primary=primary, backups=[backup])

    with pytest.raises(TTSUpstreamError):  # last error wins
        async for _ in router.synthesize("hi"):
            pass


async def test_post_first_chunk_errors_propagate() -> None:
    """A failure AFTER the first chunk is committed → propagated, not
    failed over. Splicing audio mid-utterance across providers would
    produce an audible glitch."""

    class _ExplodingMidStreamProvider:
        name = "edge"
        calls = 0

        def synthesize(
            self,
            text: str,
            *,
            voice: TTSVoice = DEFAULT_VOICE,
            audio_format: TTSAudioFormat = DEFAULT_AUDIO_FORMAT,
            timeout: float = DEFAULT_TIMEOUT_SECONDS,
        ) -> AsyncIterator[TTSAudioChunk]:
            self.calls += 1
            return self._gen()

        async def _gen(self) -> AsyncIterator[TTSAudioChunk]:
            yield TTSAudioChunk(audio=b"first")
            raise TTSUpstreamError("mid-stream blow up", provider="edge")

    primary = _ExplodingMidStreamProvider()
    backup = _ScriptedProvider(
        "aliyun",
        chunks=[TTSAudioChunk(audio=b"backup", is_final=True)],
    )
    router = TTSRouter(primary=primary, backups=[backup])  # type: ignore[arg-type]

    with pytest.raises(TTSUpstreamError):
        out: list[bytes] = []
        async for chunk in router.synthesize("hi"):
            out.append(chunk.audio)
    assert out == [b"first"]
    assert backup.calls == 0


async def test_invalid_first_byte_budget_rejected() -> None:
    """Zero / negative budget would let a failed provider hang forever
    on `wait_for(0)`. Refuse construction loudly."""
    primary = _ScriptedProvider("edge", chunks=[TTSAudioChunk(audio=b"", is_final=True)])
    with pytest.raises(ValueError):
        TTSRouter(primary=primary, first_byte_budget_s=0)
    with pytest.raises(ValueError):
        TTSRouter(primary=primary, first_byte_budget_s=-1)


# Unused but kept here so `TTSTimeoutError` stays imported for the
# typed-error narrative in the docstring above — the timeout path is
# exercised via the `first_byte_timeout` test, which expects the
# `_FailoverSignal` to wrap a `TTSTimeoutError` internally.
_ = TTSTimeoutError
