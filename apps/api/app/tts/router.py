"""Failover-aware `TTSProvider` built from an ordered chain of backends.

Mirrors `app.llm.router.LLMRouter`. Foundation §3.4.2 says Edge TTS is
the primary and Aliyun TTS is the backup; constraint #4 requires every
LLM/ASR/TTS path to support a primary→backup switch. The acceptance
behaviour: if the primary fails before producing its first audio chunk
(connection refused, auth rejected, slow handshake), fall over to the
next provider within `first_byte_budget_s`. Once the first chunk has
been forwarded, we commit to the current provider — splicing audio
mid-stream from two providers would produce an audible glitch.

Policy
------
* Try providers in order: primary, then each backup.
* For each provider, wait at most `first_byte_budget_s` (default 1.5s
  — TTS handshake is slower than LLM first-token) for the first chunk.
  If we don't get one in time *or* the provider raises any `TTSError`
  before yielding, close that stream and move on.
* Once the first chunk has been forwarded to the caller, commit to
  the current provider. Any error after that point bubbles up.
* If every provider fails before first chunk, raise the last error so
  the caller still sees a typed `TTSError`.

Why the budget is 1.5s vs LLM's 0.8s
------------------------------------
Edge TTS's first-byte budget is naturally higher than DeepSeek's
because the synthesizer must compute audio frames; observed median
~600ms, p95 ~1200ms. 1.5s leaves headroom without making a healthy
session feel sluggish.
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import AsyncIterator, Sequence

import structlog

from app.tts.errors import TTSError, TTSTimeoutError, TTSUpstreamError
from app.tts.provider import (
    DEFAULT_AUDIO_FORMAT,
    DEFAULT_TIMEOUT_SECONDS,
    DEFAULT_VOICE,
    TTSAudioChunk,
    TTSAudioFormat,
    TTSProvider,
    TTSVoice,
)

logger = structlog.get_logger(__name__)

ROUTER_NAME = "router"
DEFAULT_FIRST_BYTE_BUDGET_S = 1.5


class TTSRouter:
    """Streaming TTS router with sequential failover.

    Structurally implements `app.tts.provider.TTSProvider` so callers
    can depend on the same surface whether they talk to one provider
    or a routed chain.
    """

    name = ROUTER_NAME

    def __init__(
        self,
        *,
        primary: TTSProvider,
        backups: Sequence[TTSProvider] = (),
        first_byte_budget_s: float = DEFAULT_FIRST_BYTE_BUDGET_S,
    ) -> None:
        if first_byte_budget_s <= 0:
            raise ValueError("first_byte_budget_s must be positive")
        self._chain: tuple[TTSProvider, ...] = (primary, *backups)
        self._first_byte_budget_s = first_byte_budget_s

    async def synthesize(
        self,
        text: str,
        *,
        voice: TTSVoice = DEFAULT_VOICE,
        audio_format: TTSAudioFormat = DEFAULT_AUDIO_FORMAT,
        timeout: float = DEFAULT_TIMEOUT_SECONDS,
    ) -> AsyncIterator[TTSAudioChunk]:
        last_error: TTSError | None = None

        for attempt, provider in enumerate(self._chain):
            stream = provider.synthesize(
                text,
                voice=voice,
                audio_format=audio_format,
                timeout=timeout,
            )
            try:
                first_chunk = await self._await_first_chunk(stream, provider.name)
            except _FailoverSignal as signal:
                last_error = signal.cause
                logger.info(
                    "tts_failover",
                    provider=provider.name,
                    attempt=attempt,
                    reason=type(signal.cause).__name__,
                    error=str(signal.cause),
                )
                continue

            # First chunk in hand — commit to this provider and stream
            # the rest. Errors past this point are NOT failed over.
            yield first_chunk
            if first_chunk.is_final:
                # Single-chunk synthesis is legal (very short text + an
                # all-in-one vendor); don't enter the inner loop or we
                # would block on the next recv after the terminal flag.
                return
            async for chunk in stream:
                yield chunk
            return

        assert last_error is not None, "router chain was unexpectedly empty"
        raise last_error

    async def _await_first_chunk(
        self,
        stream: AsyncIterator[TTSAudioChunk],
        provider_name: str,
    ) -> TTSAudioChunk:
        """Pull the first chunk from `stream` or raise `_FailoverSignal`.

        Any failure mode that should trigger a retry on the next
        provider gets normalised into a `_FailoverSignal` carrying a
        typed `TTSError` so the outer loop only has one shape to
        match.
        """
        try:
            chunk = await asyncio.wait_for(
                stream.__anext__(),  # type: ignore[arg-type]
                self._first_byte_budget_s,
            )
        except TimeoutError as exc:
            await _close_stream_quietly(stream)
            raise _FailoverSignal(
                TTSTimeoutError(
                    f"{provider_name} did not produce first chunk within "
                    f"{self._first_byte_budget_s}s",
                    provider=provider_name,
                )
            ) from exc
        except StopAsyncIteration as exc:
            raise _FailoverSignal(
                TTSUpstreamError(
                    f"{provider_name} returned an empty stream",
                    provider=provider_name,
                )
            ) from exc
        except TTSError as exc:
            await _close_stream_quietly(stream)
            raise _FailoverSignal(exc) from exc

        return chunk


class _FailoverSignal(Exception):
    """Internal control-flow exception — see `_await_first_chunk`."""

    def __init__(self, cause: TTSError) -> None:
        super().__init__(str(cause))
        self.cause = cause


async def _close_stream_quietly(stream: AsyncIterator[TTSAudioChunk]) -> None:
    """Best-effort cleanup of an async iterator we're abandoning.

    Mirrors `app.llm.router._close_stream_quietly`. Errors are
    swallowed because we're on the failover path — the original cause
    is what the caller needs to see.
    """
    aclose = getattr(stream, "aclose", None)
    if aclose is None:
        return
    with contextlib.suppress(Exception):
        await aclose()
