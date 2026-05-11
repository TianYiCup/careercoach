"""Failover-aware `LLMProvider` built from an ordered chain of backends.

Foundation §3.4.1 — DeepSeek is the primary, Qwen is the backup; the
acceptance test for the spike is "induce 401 on DeepSeek, Qwen must
take over within 800ms". This module implements that switch.

Policy
------
* Try providers in order: primary, then each backup.
* For each provider, wait at most `first_byte_budget_s` (default 0.8s)
  for the first text chunk. If we don't get one in time *or* the
  provider raises any `LLMError` before yielding, close that stream
  and move on.
* Once the first chunk has been forwarded to the caller, commit to
  the current provider. Any error after that point bubbles up — we
  cannot transparently resume mid-stream without garbling the user's
  output.
* If every provider fails before first byte, raise the last error so
  the caller still sees a typed `LLMError`.
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import AsyncIterator, Sequence

import structlog

from app.llm.errors import LLMError, LLMTimeoutError, LLMUpstreamError
from app.llm.provider import (
    DEFAULT_TEMPERATURE,
    DEFAULT_TIMEOUT_SECONDS,
    LLMProvider,
)
from app.llm.types import Message

logger = structlog.get_logger(__name__)

ROUTER_NAME = "router"
DEFAULT_FIRST_BYTE_BUDGET_S = 0.8


class LLMRouter:
    """Streaming chat router with sequential failover.

    Structurally implements `app.llm.provider.LLMProvider` so callers
    (services, agents) can depend on the same surface whether they
    talk to one provider or a routed chain.
    """

    name = ROUTER_NAME

    def __init__(
        self,
        *,
        primary: LLMProvider,
        backups: Sequence[LLMProvider] = (),
        first_byte_budget_s: float = DEFAULT_FIRST_BYTE_BUDGET_S,
    ) -> None:
        if first_byte_budget_s <= 0:
            raise ValueError("first_byte_budget_s must be positive")
        self._chain: tuple[LLMProvider, ...] = (primary, *backups)
        self._first_byte_budget_s = first_byte_budget_s

    async def stream_chat(
        self,
        messages: list[Message],
        *,
        temperature: float = DEFAULT_TEMPERATURE,
        timeout: float = DEFAULT_TIMEOUT_SECONDS,
    ) -> AsyncIterator[str]:
        last_error: LLMError | None = None

        for attempt, provider in enumerate(self._chain):
            stream = provider.stream_chat(messages, temperature=temperature, timeout=timeout)
            try:
                first_chunk = await self._await_first_chunk(stream, provider.name)
            except _FailoverSignal as signal:
                last_error = signal.cause
                logger.info(
                    "llm_failover",
                    provider=provider.name,
                    attempt=attempt,
                    reason=type(signal.cause).__name__,
                    error=str(signal.cause),
                )
                continue

            # First chunk in hand — commit to this provider and stream
            # the rest. Errors past this point are NOT failed over.
            yield first_chunk
            async for chunk in stream:
                yield chunk
            return

        # Every provider failed before first byte. Re-raise the last
        # typed error so callers get a familiar exception type.
        assert last_error is not None, "router chain was unexpectedly empty"
        raise last_error

    async def _await_first_chunk(self, stream: AsyncIterator[str], provider_name: str) -> str:
        """Pull the first chunk from `stream` or raise `_FailoverSignal`.

        Any failure mode that should trigger a retry on the next
        provider gets normalised into a `_FailoverSignal` carrying a
        typed `LLMError` so the outer loop only has one shape to
        match.
        """
        try:
            chunk = await asyncio.wait_for(stream.__anext__(), self._first_byte_budget_s)
        except TimeoutError as exc:
            await _close_stream_quietly(stream)
            raise _FailoverSignal(
                LLMTimeoutError(
                    f"{provider_name} did not produce first chunk within "
                    f"{self._first_byte_budget_s}s",
                    provider=provider_name,
                )
            ) from exc
        except StopAsyncIteration as exc:
            raise _FailoverSignal(
                LLMUpstreamError(
                    f"{provider_name} returned an empty stream",
                    provider=provider_name,
                )
            ) from exc
        except LLMError as exc:
            await _close_stream_quietly(stream)
            raise _FailoverSignal(exc) from exc

        return chunk


class _FailoverSignal(Exception):
    """Internal control-flow exception — see `_await_first_chunk`."""

    def __init__(self, cause: LLMError) -> None:
        super().__init__(str(cause))
        self.cause = cause


async def _close_stream_quietly(stream: AsyncIterator[str]) -> None:
    """Best-effort cleanup of an async iterator we're abandoning.

    The adapter's `async with client.stream(...)` cleanup runs when
    we call `aclose()`. We swallow errors here because we're already
    on the failover path — the original cause is what the caller
    needs to see.
    """
    aclose = getattr(stream, "aclose", None)
    if aclose is None:
        return
    with contextlib.suppress(Exception):
        await aclose()
