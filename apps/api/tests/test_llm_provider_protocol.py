"""Contract tests for the LLM abstraction layer (foundation §3.3.4).

These tests pin the shape of the public surface so adapters in
follow-up PRs (DeepSeek, Qwen) can rely on it without surprise
changes.
"""

from collections.abc import AsyncIterator

import pytest
from app.llm import (
    LLMAuthError,
    LLMError,
    LLMProvider,
    LLMRateLimitError,
    LLMTimeoutError,
    LLMUpstreamError,
    Message,
    Role,
    TokenUsage,
)
from pydantic import ValidationError


class _FakeProvider:
    """Minimal structural implementation used to verify the Protocol."""

    name = "fake"

    async def stream_chat(
        self,
        messages: list[Message],
        *,
        temperature: float = 0.7,
        timeout: float = 8.0,
        usage_sink: list[TokenUsage] | None = None,
        max_tokens: int | None = None,
    ) -> AsyncIterator[str]:
        # Echo the last user message back as two chunks so tests can
        # assert streaming semantics. We accept `usage_sink` /
        # `max_tokens` for protocol-compat (A-27 / perf-E) but ignore
        # them — the fake doesn't synthesise token counts or truncate.
        _ = (usage_sink, max_tokens)
        last = messages[-1].content
        yield last[: len(last) // 2]
        yield last[len(last) // 2 :]


def test_fake_provider_satisfies_protocol() -> None:
    provider: LLMProvider = _FakeProvider()
    assert isinstance(provider, LLMProvider)
    assert provider.name == "fake"


async def test_stream_chat_yields_deltas_in_order() -> None:
    provider: LLMProvider = _FakeProvider()
    msgs = [Message.system("you are K"), Message.user("hello world")]

    chunks = [chunk async for chunk in provider.stream_chat(msgs)]

    assert "".join(chunks) == "hello world"
    assert len(chunks) == 2


def test_message_helpers_set_correct_role() -> None:
    assert Message.system("x").role is Role.SYSTEM
    assert Message.user("x").role is Role.USER
    assert Message.assistant("x").role is Role.ASSISTANT


def test_message_is_frozen() -> None:
    msg = Message.user("hi")
    with pytest.raises(ValidationError):
        msg.content = "mutated"  # type: ignore[misc]


def test_message_rejects_empty_content() -> None:
    with pytest.raises(ValidationError):
        Message.user("")


def test_message_rejects_extra_fields() -> None:
    with pytest.raises(ValidationError):
        Message.model_validate({"role": "user", "content": "hi", "extra": 1})


@pytest.mark.parametrize(
    "exc_cls",
    [LLMTimeoutError, LLMAuthError, LLMRateLimitError, LLMUpstreamError],
)
def test_error_subclasses_inherit_from_base(exc_cls: type[LLMError]) -> None:
    assert issubclass(exc_cls, LLMError)


def test_upstream_error_carries_status_code() -> None:
    err = LLMUpstreamError("boom", provider="deepseek", status_code=502)
    assert err.provider == "deepseek"
    assert err.status_code == 502
    assert str(err) == "boom"


def test_base_error_keeps_provider_optional() -> None:
    err = LLMTimeoutError("slow")
    assert err.provider is None
