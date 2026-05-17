"""Shared helpers for OpenAI-compatible streaming chat APIs.

DeepSeek and DashScope (Qwen) both expose the same `POST /v1/chat/
completions` shape with SSE response framing, so the SSE parsing and
status-code error mapping live here, not duplicated per adapter.

This module is private (`_` prefix) — adapters import from it, callers
outside the `llm` package should not.
"""

from __future__ import annotations

import json
from typing import Any, Final

import httpx

from app.llm.errors import (
    LLMAuthError,
    LLMRateLimitError,
    LLMTimeoutError,
    LLMUpstreamError,
)
from app.llm.types import Message, TokenUsage

CHAT_COMPLETIONS_PATH: Final = "/v1/chat/completions"
_SSE_PREFIX: Final = "data: "
_SSE_DONE: Final = "[DONE]"

# Module-level sentinel so callers can distinguish "stream finished"
# from "empty / skip-this-line".
DONE: Final[object] = object()


def build_chat_request_body(
    messages: list[Message],
    *,
    model: str,
    temperature: float,
    include_usage: bool = False,
) -> dict[str, Any]:
    body: dict[str, Any] = {
        "model": model,
        "stream": True,
        "temperature": temperature,
        "messages": [{"role": m.role.value, "content": m.content} for m in messages],
    }
    if include_usage:
        # OpenAI / DeepSeek / Qwen all honour this — the upstream
        # appends one extra SSE chunk at end-of-stream carrying the
        # `usage` payload (and an empty `choices` array). Without
        # this flag the usage chunk is suppressed entirely.
        body["stream_options"] = {"include_usage": True}
    return body


def parse_sse_line(line: str) -> str | TokenUsage | object | None:
    """Return one of:

    * the `DONE` sentinel — end of stream
    * a non-empty `str` — text delta
    * a `TokenUsage` — the upstream's end-of-stream accounting chunk
      (only sent when the request body set `stream_options.include_usage`)
    * `None` — skip this line (keep-alive, malformed, empty delta)
    """
    if not line or not line.startswith(_SSE_PREFIX):
        return None
    payload = line[len(_SSE_PREFIX) :].strip()
    if payload == _SSE_DONE:
        return DONE
    try:
        obj = json.loads(payload)
    except json.JSONDecodeError:
        return None
    choices = obj.get("choices") or []
    # The usage-bearing chunk has an empty `choices` list and a
    # populated `usage` dict. Check it first so the delta path stays
    # untouched on every normal text chunk.
    if not choices:
        return _parse_usage(obj.get("usage"))
    delta = choices[0].get("delta") or {}
    return delta.get("content") or None


def _parse_usage(usage: Any) -> TokenUsage | None:
    """Lift a vendor `usage` dict into our `TokenUsage`, or `None` on bad shape."""
    if not isinstance(usage, dict):
        return None
    try:
        return TokenUsage(
            prompt_tokens=int(usage.get("prompt_tokens", 0)),
            completion_tokens=int(usage.get("completion_tokens", 0)),
            total_tokens=int(usage.get("total_tokens", 0)),
        )
    except (TypeError, ValueError):
        return None


def map_status_to_error(*, status_code: int, body_text: str, provider: str) -> Exception:
    snippet = body_text[:200]
    if status_code in (401, 403):
        return LLMAuthError(
            f"{provider} auth failed ({status_code}): {snippet}",
            provider=provider,
        )
    if status_code == 429:
        return LLMRateLimitError(
            f"{provider} rate limited ({status_code}): {snippet}",
            provider=provider,
        )
    return LLMUpstreamError(
        f"{provider} upstream error ({status_code}): {snippet}",
        provider=provider,
        status_code=status_code,
    )


def map_transport_exc(*, exc: Exception, provider: str, timeout: float) -> Exception:
    """Translate an httpx transport exception into our error hierarchy."""
    if isinstance(exc, httpx.TimeoutException):
        return LLMTimeoutError(
            f"{provider} request exceeded {timeout}s",
            provider=provider,
        )
    return LLMUpstreamError(
        f"{provider} transport error: {exc}",
        provider=provider,
    )


async def safe_response_text(response: httpx.Response) -> str:
    try:
        await response.aread()
        return response.text
    except httpx.HTTPError:
        return ""
