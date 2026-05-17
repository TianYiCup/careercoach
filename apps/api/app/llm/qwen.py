"""Qwen / DashScope adapter — backup LLM provider per foundation §3.4.1.

DashScope exposes an OpenAI-compatible endpoint at
`https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions`
that accepts the same streaming wire format as DeepSeek, so the
shared `_openai_compat` helpers carry the per-line + per-error
plumbing and this file is mostly just per-provider defaults.

Foundation §3.4.1 picks `qwen-max` as the cross-check model; callers
can override via `model=`.
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
from app.llm.types import Message, TokenUsage

PROVIDER_NAME = "qwen"
DEFAULT_BASE_URL = "https://dashscope.aliyuncs.com/compatible-mode"


class QwenProvider:
    """OpenAI-compatible streaming chat client for Alibaba DashScope.

    Structurally implements `app.llm.provider.LLMProvider`. Mirrors
    `DeepSeekProvider` deliberately — the only differences are the
    defaults and `name`, so the router can treat them symmetrically.
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
            raise ValueError("qwen api_key is empty")
        self._api_key = api_key
        self._model = model
        self._base_url = base_url.rstrip("/")
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
        usage_sink: list[TokenUsage] | None = None,
    ) -> AsyncIterator[str]:
        if not messages:
            raise ValueError("messages must not be empty")

        client = self._ensure_client()
        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Accept": "text/event-stream",
        }
        body = build_chat_request_body(
            messages,
            model=self._model,
            temperature=temperature,
            include_usage=usage_sink is not None,
        )

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
                    if isinstance(chunk, TokenUsage):
                        if usage_sink is not None:
                            usage_sink.append(chunk)
                        continue
                    if isinstance(chunk, str) and chunk:
                        yield chunk
        except httpx.HTTPError as exc:
            raise map_transport_exc(exc=exc, provider=PROVIDER_NAME, timeout=timeout) from exc
