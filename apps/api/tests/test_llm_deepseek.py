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
    TokenUsage,
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


# --- A-27: token usage on streamed responses ---


def _sse_body_with_usage(
    *chunks: str,
    prompt_tokens: int,
    completion_tokens: int,
    total_tokens: int,
) -> bytes:
    """Body that DeepSeek returns when `stream_options.include_usage=true`.

    DeepSeek (and OpenAI) append a final SSE chunk with an empty
    `choices` list and a populated `usage` block, right before the
    `[DONE]` sentinel.
    """
    lines: list[str] = []
    for c in chunks:
        payload = '{"choices":[{"index":0,"delta":{"content":"' + c + '"},"finish_reason":null}]}'
        lines.append(f"data: {payload}")
    usage_payload = (
        '{"choices":[],"usage":{'
        f'"prompt_tokens":{prompt_tokens},'
        f'"completion_tokens":{completion_tokens},'
        f'"total_tokens":{total_tokens}'
        "}}"
    )
    lines.append(f"data: {usage_payload}")
    lines.append("data: [DONE]")
    return ("\n".join(lines) + "\n").encode("utf-8")


async def test_usage_sink_populated_when_provided(messages: list[Message]) -> None:
    """When the caller passes a `usage_sink`, the adapter must request
    accounting from upstream (`stream_options.include_usage=true`),
    parse the final usage chunk, and append the `TokenUsage` to the
    sink. The token deltas themselves are unaffected."""
    captured_body: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured_body["body"] = json.loads(request.content)
        return httpx.Response(
            200,
            content=_sse_body_with_usage(
                "hi",
                " there",
                prompt_tokens=12,
                completion_tokens=4,
                total_tokens=16,
            ),
        )

    provider = _make_provider(handler)
    sink: list[TokenUsage] = []
    try:
        chunks = await _collect(provider.stream_chat(messages, usage_sink=sink))
    finally:
        await provider.aclose()

    assert chunks == ["hi", " there"]
    body = captured_body["body"]
    assert isinstance(body, dict)
    assert body.get("stream_options") == {"include_usage": True}
    assert sink == [TokenUsage(prompt_tokens=12, completion_tokens=4, total_tokens=16)]


async def test_usage_sink_none_means_include_usage_off(messages: list[Message]) -> None:
    """Default (sink=None) MUST omit `stream_options` so the upstream
    doesn't bill the (tiny) usage-chunk overhead on every call."""
    captured_body: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured_body["body"] = json.loads(request.content)
        return httpx.Response(200, content=_sse_body("ok"))

    provider = _make_provider(handler)
    try:
        await _collect(provider.stream_chat(messages))
    finally:
        await provider.aclose()

    body = captured_body["body"]
    assert isinstance(body, dict)
    assert "stream_options" not in body


async def test_max_tokens_sent_when_provided(messages: list[Message]) -> None:
    """`max_tokens` (perf-E) must reach the upstream body so the copilot
    hint's output is actually capped at the vendor."""
    captured_body: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured_body["body"] = json.loads(request.content)
        return httpx.Response(200, content=_sse_body("ok"))

    provider = _make_provider(handler)
    try:
        await _collect(provider.stream_chat(messages, max_tokens=96))
    finally:
        await provider.aclose()

    body = captured_body["body"]
    assert isinstance(body, dict)
    assert body["max_tokens"] == 96


async def test_max_tokens_omitted_when_none(messages: list[Message]) -> None:
    """Default (None) MUST omit `max_tokens` so non-latency-sensitive
    callers keep the vendor's default completion length."""
    captured_body: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured_body["body"] = json.loads(request.content)
        return httpx.Response(200, content=_sse_body("ok"))

    provider = _make_provider(handler)
    try:
        await _collect(provider.stream_chat(messages))
    finally:
        await provider.aclose()

    body = captured_body["body"]
    assert isinstance(body, dict)
    assert "max_tokens" not in body


async def test_usage_chunk_ignored_when_sink_is_none(messages: list[Message]) -> None:
    """If an upstream sends a usage chunk anyway (because some other
    flag was set, or the API changed default behaviour), the adapter
    must silently drop it rather than yielding garbage strings."""

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            content=_sse_body_with_usage(
                "a",
                "b",
                prompt_tokens=1,
                completion_tokens=2,
                total_tokens=3,
            ),
        )

    provider = _make_provider(handler)
    try:
        chunks = await _collect(provider.stream_chat(messages))
    finally:
        await provider.aclose()

    # Only the deltas, never the usage payload.
    assert chunks == ["a", "b"]


async def test_malformed_usage_payload_does_not_crash(messages: list[Message]) -> None:
    """If the upstream sends a `usage` field with wrong types, the
    parser must yield None for that chunk so the stream keeps going.
    Sink stays empty — better no data than wrong data."""

    bad = b'data: {"choices":[],"usage":{"prompt_tokens":"oops"}}\n'
    body = b'data: {"choices":[{"delta":{"content":"x"}}]}\n' + bad + b"data: [DONE]\n"

    provider = _make_provider(lambda _r: httpx.Response(200, content=body))
    sink: list[TokenUsage] = []
    try:
        chunks = await _collect(provider.stream_chat(messages, usage_sink=sink))
    finally:
        await provider.aclose()

    assert chunks == ["x"]
    assert sink == []
