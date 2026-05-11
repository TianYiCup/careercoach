"""DeepSeek adapter — primary LLM provider per foundation §3.4.1.

DeepSeek's chat completions API is OpenAI-compatible, so the wire
format is `POST /v1/chat/completions` with `stream=true` returning
SSE chunks shaped like:

    data: {"choices":[{"delta":{"content":"..."}}]}
    data: [DONE]

This adapter is intentionally thin: build request → stream lines →
yield `delta.content` strings → map any failure into
`app.llm.errors`. No retries, no failover — those live in the router.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import Any

import httpx

from app.llm.errors import (
    LLMAuthError,
    LLMRateLimitError,
    LLMTimeoutError,
    LLMUpstreamError,
)
from app.llm.provider import DEFAULT_TEMPERATURE, DEFAULT_TIMEOUT_SECONDS
from app.llm.types import Message

PROVIDER_NAME = "deepseek"
_CHAT_PATH = "/v1/chat/completions"
_SSE_PREFIX = "data: "
_SSE_DONE = "[DONE]"


class DeepSeekProvider:
    """OpenAI-compatible streaming chat client for DeepSeek.

    Structurally implements `app.llm.provider.LLMProvider`. Holds a
    long-lived `httpx.AsyncClient` so connection pooling survives
    across calls; the application is expected to keep one instance
    for the process lifetime and close it on shutdown.
    """

    name = PROVIDER_NAME

    def __init__(
        self,
        *,
        api_key: str,
        model: str,
        base_url: str = "https://api.deepseek.com",
        client: httpx.AsyncClient | None = None,
    ) -> None:
        if not api_key:
            # Surface a clear error at construction time rather than
            # the first request — easier to diagnose in dev.
            raise ValueError("deepseek api_key is empty")
        self._api_key = api_key
        self._model = model
        self._base_url = base_url.rstrip("/")
        # Caller-supplied client wins so tests can inject MockTransport.
        # We do NOT close a client we didn't create.
        self._client = client
        self._owns_client = client is None

    async def aclose(self) -> None:
        if self._owns_client and self._client is not None:
            await self._client.aclose()
            self._client = None

    def _request_body(self, messages: list[Message], temperature: float) -> dict[str, Any]:
        return {
            "model": self._model,
            "stream": True,
            "temperature": temperature,
            "messages": [{"role": m.role.value, "content": m.content} for m in messages],
        }

    def _ensure_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(base_url=self._base_url)
        return self._client

    async def stream_chat(
        self,
        messages: list[Message],
        *,
        temperature: float = DEFAULT_TEMPERATURE,
        timeout: float = DEFAULT_TIMEOUT_SECONDS,
    ) -> AsyncIterator[str]:
        if not messages:
            raise ValueError("messages must not be empty")

        client = self._ensure_client()
        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Accept": "text/event-stream",
        }
        body = self._request_body(messages, temperature)

        try:
            async with client.stream(
                "POST",
                _CHAT_PATH,
                json=body,
                headers=headers,
                timeout=timeout,
            ) as response:
                if response.status_code >= 400:
                    raise _map_status(response.status_code, await _safe_text(response))

                async for line in response.aiter_lines():
                    chunk = _parse_sse_line(line)
                    if chunk is None:
                        continue
                    if chunk is _SENTINEL_DONE:
                        return
                    if chunk:
                        yield chunk
        except httpx.TimeoutException as exc:
            raise LLMTimeoutError(
                f"deepseek request exceeded {timeout}s",
                provider=PROVIDER_NAME,
            ) from exc
        except httpx.HTTPError as exc:
            # Network / protocol failures that aren't status-code-bearing.
            raise LLMUpstreamError(
                f"deepseek transport error: {exc}",
                provider=PROVIDER_NAME,
            ) from exc


# Module-level sentinel so we can distinguish "stream finished" from
# "empty delta we should skip".
_SENTINEL_DONE: Any = object()


def _parse_sse_line(line: str) -> Any:
    """Return None for skip, the DONE sentinel for end-of-stream, or the
    text delta string."""
    if not line or not line.startswith(_SSE_PREFIX):
        return None
    payload = line[len(_SSE_PREFIX) :].strip()
    if payload == _SSE_DONE:
        return _SENTINEL_DONE
    try:
        obj = json.loads(payload)
    except json.JSONDecodeError:
        # Tolerate keep-alive comments / malformed lines: DeepSeek
        # never sends them today, but we don't want one bad chunk to
        # kill the whole stream.
        return None
    choices = obj.get("choices") or []
    if not choices:
        return None
    delta = choices[0].get("delta") or {}
    return delta.get("content") or None


def _map_status(status_code: int, body_text: str) -> Exception:
    snippet = body_text[:200]
    if status_code in (401, 403):
        return LLMAuthError(
            f"deepseek auth failed ({status_code}): {snippet}",
            provider=PROVIDER_NAME,
        )
    if status_code == 429:
        return LLMRateLimitError(
            f"deepseek rate limited ({status_code}): {snippet}",
            provider=PROVIDER_NAME,
        )
    return LLMUpstreamError(
        f"deepseek upstream error ({status_code}): {snippet}",
        provider=PROVIDER_NAME,
        status_code=status_code,
    )


async def _safe_text(response: httpx.Response) -> str:
    try:
        await response.aread()
        return response.text
    except httpx.HTTPError:
        return ""
