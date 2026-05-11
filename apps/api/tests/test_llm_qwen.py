"""Qwen / DashScope adapter tests — wire format + error mapping.

Mirrors `test_llm_deepseek.py` since both adapters share the
OpenAI-compatible helpers. The duplication is deliberate: each
adapter's wire contract is asserted independently so a vendor-specific
quirk introduced later can't quietly bypass tests on the other side.
"""

import json
from collections.abc import AsyncIterator

import httpx
import pytest
from app.llm import (
    LLMAuthError,
    LLMRateLimitError,
    LLMTimeoutError,
    LLMUpstreamError,
    Message,
    QwenProvider,
)

_BASE_URL = "https://dashscope.aliyuncs.com/compatible-mode"


def _sse_body(*chunks: str) -> bytes:
    lines: list[str] = []
    for c in chunks:
        payload = '{"choices":[{"index":0,"delta":{"content":"' + c + '"},"finish_reason":null}]}'
        lines.append(f"data: {payload}")
    lines.append("data: [DONE]")
    return ("\n".join(lines) + "\n").encode("utf-8")


def _make_provider(handler: object, *, api_key: str = "test-key") -> QwenProvider:
    transport = httpx.MockTransport(handler)  # type: ignore[arg-type]
    client = httpx.AsyncClient(transport=transport, base_url=_BASE_URL)
    return QwenProvider(
        api_key=api_key,
        model="qwen-max",
        base_url=_BASE_URL,
        client=client,
    )


@pytest.fixture
def messages() -> list[Message]:
    return [Message.system("you are K"), Message.user("hi")]


async def _collect(it: AsyncIterator[str]) -> list[str]:
    return [chunk async for chunk in it]


async def test_construct_rejects_empty_api_key() -> None:
    with pytest.raises(ValueError, match="qwen api_key is empty"):
        QwenProvider(api_key="", model="qwen-max")


async def test_stream_chat_hits_compat_endpoint(messages: list[Message]) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        # Compat mode lives under /compatible-mode on DashScope; the
        # adapter MUST not drop that prefix when composing the URL.
        assert request.url.path == "/compatible-mode/v1/chat/completions"
        assert request.headers["authorization"] == "Bearer test-key"
        return httpx.Response(200, content=_sse_body("nihao"))

    provider = _make_provider(handler)
    try:
        chunks = await _collect(provider.stream_chat(messages))
    finally:
        await provider.aclose()

    assert chunks == ["nihao"]


async def test_request_body_uses_configured_model(messages: list[Message]) -> None:
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = json.loads(request.content)
        return httpx.Response(200, content=_sse_body("ok"))

    provider = _make_provider(handler)
    try:
        await _collect(provider.stream_chat(messages, temperature=0.4))
    finally:
        await provider.aclose()

    body = captured["body"]
    assert isinstance(body, dict)
    assert body["model"] == "qwen-max"
    assert body["stream"] is True
    assert body["temperature"] == 0.4
    assert body["messages"] == [
        {"role": "system", "content": "you are K"},
        {"role": "user", "content": "hi"},
    ]


async def test_empty_messages_raises_value_error() -> None:
    provider = _make_provider(lambda _r: httpx.Response(200))
    try:
        with pytest.raises(ValueError, match="messages must not be empty"):
            await _collect(provider.stream_chat([]))
    finally:
        await provider.aclose()


@pytest.mark.parametrize(
    ("status", "exc_cls"),
    [
        (401, LLMAuthError),
        (403, LLMAuthError),
        (429, LLMRateLimitError),
        (500, LLMUpstreamError),
        (502, LLMUpstreamError),
    ],
)
async def test_http_errors_map_to_typed_exceptions(
    status: int,
    exc_cls: type[Exception],
    messages: list[Message],
) -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(status, json={"error": {"message": "nope"}})

    provider = _make_provider(handler)
    try:
        with pytest.raises(exc_cls) as ei:
            await _collect(provider.stream_chat(messages))
    finally:
        await provider.aclose()

    err = ei.value
    assert getattr(err, "provider", None) == "qwen"
    if exc_cls is LLMUpstreamError:
        assert getattr(err, "status_code", None) == status


async def test_timeout_maps_to_llm_timeout_error(messages: list[Message]) -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectTimeout("simulated timeout")

    provider = _make_provider(handler)
    try:
        with pytest.raises(LLMTimeoutError) as ei:
            await _collect(provider.stream_chat(messages))
    finally:
        await provider.aclose()

    assert ei.value.provider == "qwen"


async def test_other_transport_errors_map_to_upstream(messages: list[Message]) -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused")

    provider = _make_provider(handler)
    try:
        with pytest.raises(LLMUpstreamError) as ei:
            await _collect(provider.stream_chat(messages))
    finally:
        await provider.aclose()

    assert ei.value.provider == "qwen"
