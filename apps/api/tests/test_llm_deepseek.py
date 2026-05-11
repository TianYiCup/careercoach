"""DeepSeek adapter tests — wire format + error mapping.

Uses httpx.MockTransport so we never hit the network. Each test
constructs the upstream response (status + SSE body), runs the
adapter, and asserts the surface contract: yielded chunks for the
happy path, our typed errors for failures.
"""

import json
from collections.abc import AsyncIterator

import httpx
import pytest
from app.llm import (
    DeepSeekProvider,
    LLMAuthError,
    LLMRateLimitError,
    LLMTimeoutError,
    LLMUpstreamError,
    Message,
)


def _sse_body(*chunks: str) -> bytes:
    """Build a DeepSeek-style SSE response body from delta strings.

    Each chunk becomes one `data: {...}` line plus a final `data: [DONE]`.
    """
    lines: list[str] = []
    for c in chunks:
        payload = '{"choices":[{"index":0,"delta":{"content":"' + c + '"},"finish_reason":null}]}'
        lines.append(f"data: {payload}")
    lines.append("data: [DONE]")
    return ("\n".join(lines) + "\n").encode("utf-8")


def _make_provider(
    handler: object,
    *,
    api_key: str = "test-key",
) -> DeepSeekProvider:
    transport = httpx.MockTransport(handler)  # type: ignore[arg-type]
    client = httpx.AsyncClient(transport=transport, base_url="https://api.deepseek.com")
    return DeepSeekProvider(
        api_key=api_key,
        model="deepseek-chat",
        base_url="https://api.deepseek.com",
        client=client,
    )


@pytest.fixture
def messages() -> list[Message]:
    return [Message.system("you are K"), Message.user("hi")]


async def _collect(it: AsyncIterator[str]) -> list[str]:
    return [chunk async for chunk in it]


async def test_construct_rejects_empty_api_key() -> None:
    with pytest.raises(ValueError, match="api_key is empty"):
        DeepSeekProvider(api_key="", model="deepseek-chat")


async def test_stream_chat_yields_deltas_in_order(messages: list[Message]) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v1/chat/completions"
        assert request.headers["authorization"] == "Bearer test-key"
        return httpx.Response(200, content=_sse_body("hello", " ", "world"))

    provider = _make_provider(handler)
    try:
        chunks = await _collect(provider.stream_chat(messages))
    finally:
        await provider.aclose()

    assert chunks == ["hello", " ", "world"]


async def test_request_body_serializes_messages(messages: list[Message]) -> None:
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = json.loads(request.content)
        return httpx.Response(200, content=_sse_body("ok"))

    provider = _make_provider(handler)
    try:
        await _collect(provider.stream_chat(messages, temperature=0.3))
    finally:
        await provider.aclose()

    body = captured["body"]
    assert isinstance(body, dict)
    assert body["model"] == "deepseek-chat"
    assert body["stream"] is True
    assert body["temperature"] == 0.3
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


async def test_done_sentinel_terminates_stream(messages: list[Message]) -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        # Send DONE before any content — adapter must not yield anything.
        return httpx.Response(200, content=b"data: [DONE]\n")

    provider = _make_provider(handler)
    try:
        chunks = await _collect(provider.stream_chat(messages))
    finally:
        await provider.aclose()

    assert chunks == []


async def test_malformed_chunks_are_skipped(messages: list[Message]) -> None:
    body = (
        b"\n"
        b": keep-alive\n"
        b'data: {"choices":[{"delta":{"content":"a"}}]}\n'
        b"data: not-json\n"
        b'data: {"choices":[]}\n'
        b'data: {"choices":[{"delta":{"content":"b"}}]}\n'
        b"data: [DONE]\n"
    )

    provider = _make_provider(lambda _r: httpx.Response(200, content=body))
    try:
        chunks = await _collect(provider.stream_chat(messages))
    finally:
        await provider.aclose()

    assert chunks == ["a", "b"]


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
    assert getattr(err, "provider", None) == "deepseek"
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

    assert ei.value.provider == "deepseek"


async def test_other_transport_errors_map_to_upstream(messages: list[Message]) -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused")

    provider = _make_provider(handler)
    try:
        with pytest.raises(LLMUpstreamError) as ei:
            await _collect(provider.stream_chat(messages))
    finally:
        await provider.aclose()

    assert ei.value.provider == "deepseek"
