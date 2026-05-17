"""`LLMProvider` Protocol — the only contract the rest of the app sees.

The concrete adapters (DeepSeek, Qwen, ...) implement this Protocol
structurally; no `class FooAdapter(LLMProvider)` inheritance is
required, which keeps adapter modules free of cross-package imports.

Foundation §3.3.4 fixes the shape:

    async def stream_chat(
        self,
        messages: list[Message],
        temperature: float = 0.7,
        timeout: float = 8.0,
    ) -> AsyncIterator[str]: ...

Implementations MUST:
  * raise from `app.llm.errors` on failure (never bubble httpx errors)
  * yield text deltas only — control tokens / finish reasons stay
    inside the adapter
  * honour `timeout` end-to-end (connect + read + total)
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Protocol, runtime_checkable

from app.llm.types import Message, TokenUsage

DEFAULT_TEMPERATURE = 0.7
DEFAULT_TIMEOUT_SECONDS = 8.0


@runtime_checkable
class LLMProvider(Protocol):
    """Streaming chat completion provider.

    `runtime_checkable` so the router can `isinstance` check in tests
    and dev tooling. Production code should depend on the Protocol
    structurally, not via isinstance.
    """

    name: str
    """Stable identifier used in logs and Langfuse traces (e.g. "deepseek")."""

    def stream_chat(
        self,
        messages: list[Message],
        *,
        temperature: float = DEFAULT_TEMPERATURE,
        timeout: float = DEFAULT_TIMEOUT_SECONDS,
        usage_sink: list[TokenUsage] | None = None,
    ) -> AsyncIterator[str]:
        """Stream response text deltas.

        Returns an async iterator (NOT an awaitable) so callers can
        `async for chunk in provider.stream_chat(...)` directly. The
        first iteration may perform the network connect; adapters
        should NOT do I/O before iteration begins.

        When `usage_sink` is non-None, adapters MUST request usage
        accounting from the upstream (vendors honour
        `stream_options.include_usage=true` on OpenAI-compatible APIs)
        and append the parsed `TokenUsage` to the list. Callers can
        then read `sink[0]` after the stream is fully consumed and
        forward it to Langfuse for cost analytics. When `usage_sink`
        is None, adapters MUST NOT request usage (saves a small bit
        of upstream cost on every call). Tests stubs MAY ignore the
        kwarg entirely — the sink stays empty and the caller falls
        back to no-usage behaviour.
        """
        ...
