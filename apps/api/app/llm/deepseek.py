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

from collections.abc import AsyncIterator

import httpx

from app.llm._openai_compat import (
    CHAT_COMPLETIONS_PATH,
    DONE,
    build_chat_request_body,
    map_status_to_error,
    map_transport_exc,
    parse_sse_line,
    safe_response_text,
)
from app.llm.provider import DEFAULT_TEMPERATURE, DEFAULT_TIMEOUT_SECONDS
from app.llm.types import Message

PROVIDER_NAME = "deepseek"
DEFAULT_BASE_URL = "https://api.deepseek.com"


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
        base_url: str = DEFAULT_BASE_URL,
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
        body = build_chat_request_body(messages, model=self._model, temperature=temperature)

        try:
            async with client.stream(
                "POST",
                CHAT_COMPLETIONS_PATH,
                json=body,
                headers=headers,
                timeout=timeout,
            ) as response:
                if response.status_code >= 400:
                    raise map_status_to_error(
                        status_code=response.status_code,
                        body_text=await safe_response_text(response),
                        provider=PROVIDER_NAME,
                    )

                async for line in response.aiter_lines():
                    chunk = parse_sse_line(line)
                    if chunk is None:
                        continue
                    if chunk is DONE:
                        return
                    if isinstance(chunk, str) and chunk:
                        yield chunk
        except httpx.HTTPError as exc:
            raise map_transport_exc(exc=exc, provider=PROVIDER_NAME, timeout=timeout) from exc
