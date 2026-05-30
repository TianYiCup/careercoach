"""LLMRouter failover tests (foundation §3.4.1).

Backed by tiny in-process fake providers so we can deterministically
trigger every failure mode (auth fail, rate limit, mid-stream error,
stall before first byte, empty stream, etc.) without httpx.
"""

import asyncio
import time
from collections.abc import AsyncIterator

import pytest
from app.llm import (
    LLMAuthError,
    LLMError,
    LLMProvider,
    LLMRateLimitError,
    LLMRouter,
    LLMTimeoutError,
    LLMUpstreamError,
    Message,
    TokenUsage,
)


class _FakeProvider:
    """In-memory `LLMProvider` with scripted behaviour.

    * `chunks` — what to yield once streaming starts.
    * `first_byte_delay` — sleep before yielding the first chunk.
    * `pre_stream_error` — raise before producing any chunks (auth /
      rate-limit / etc. style failure).
    * `mid_stream_error` — raise after the first chunk has been
      yielded (should NOT trigger failover).
    """

    def __init__(
        self,
        name: str,
        *,
        chunks: list[str] | None = None,
        first_byte_delay: float = 0.0,
        pre_stream_error: LLMError | None = None,
        mid_stream_error: LLMError | None = None,
    ) -> None:
        self.name = name
        self._chunks = chunks or []
        self._first_byte_delay = first_byte_delay
        self._pre_stream_error = pre_stream_error
        self._mid_stream_error = mid_stream_error
        self.invocations = 0
        self.aclose_calls = 0

    async def stream_chat(
        self,
        messages: list[Message],
        *,
        temperature: float = 0.7,
        timeout: float = 8.0,
        usage_sink: list[TokenUsage] | None = None,
        max_tokens: int | None = None,
    ) -> AsyncIterator[str]:
        _ = (usage_sink, max_tokens)
        self.invocations += 1
        if self._pre_stream_error is not None:
            raise self._pre_stream_error
        if self._first_byte_delay:
            await asyncio.sleep(self._first_byte_delay)
        for index, chunk in enumerate(self._chunks):
            yield chunk
            if self._mid_stream_error is not None and index == 0:
                raise self._mid_stream_error


@pytest.fixture
def messages() -> list[Message]:
    return [Message.system("you are K"), Message.user("hi")]


async def _collect(it: AsyncIterator[str]) -> list[str]:
    return [chunk async for chunk in it]


async def test_primary_success_skips_backups(messages: list[Message]) -> None:
    primary = _FakeProvider("primary", chunks=["he", "llo"])
    backup = _FakeProvider("backup", chunks=["should-not-fire"])

    router = LLMRouter(primary=primary, backups=[backup])
    chunks = await _collect(router.stream_chat(messages))

    assert chunks == ["he", "llo"]
    assert backup.invocations == 0


@pytest.mark.parametrize(
    "error",
    [
        LLMAuthError("401", provider="primary"),
        LLMRateLimitError("429", provider="primary"),
        LLMUpstreamError("502", provider="primary", status_code=502),
        LLMTimeoutError("slow", provider="primary"),
    ],
)
async def test_pre_stream_error_falls_over_to_backup(
    error: LLMError, messages: list[Message]
) -> None:
    primary = _FakeProvider("primary", pre_stream_error=error)
    backup = _FakeProvider("backup", chunks=["from", "-backup"])

    router = LLMRouter(primary=primary, backups=[backup])
    chunks = await _collect(router.stream_chat(messages))

    assert chunks == ["from", "-backup"]
    assert primary.invocations == 1
    assert backup.invocations == 1


async def test_stall_before_first_byte_falls_over_within_budget(
    messages: list[Message],
) -> None:
    # Primary never produces a chunk in time; budget is tight so the
    # whole call should resolve quickly via the backup.
    primary = _FakeProvider("primary", chunks=["nope"], first_byte_delay=2.0)
    backup = _FakeProvider("backup", chunks=["fast"])

    router = LLMRouter(primary=primary, backups=[backup], first_byte_budget_s=0.05)

    start = time.perf_counter()
    chunks = await _collect(router.stream_chat(messages))
    elapsed = time.perf_counter() - start

    assert chunks == ["fast"]
    # Generous bound: budget (50ms) + scheduling slack. Should be far
    # under the foundation-mandated 800ms.
    assert elapsed < 0.4, f"failover took {elapsed:.3f}s — over budget"


async def test_mid_stream_error_bubbles_no_failover(
    messages: list[Message],
) -> None:
    primary = _FakeProvider(
        "primary",
        chunks=["before"],
        mid_stream_error=LLMUpstreamError("died", provider="primary", status_code=500),
    )
    backup = _FakeProvider("backup", chunks=["unused"])

    router = LLMRouter(primary=primary, backups=[backup])

    received: list[str] = []
    with pytest.raises(LLMUpstreamError) as ei:
        async for chunk in router.stream_chat(messages):
            received.append(chunk)

    assert received == ["before"]
    assert ei.value.provider == "primary"
    assert backup.invocations == 0


async def test_all_providers_fail_raises_last_error(
    messages: list[Message],
) -> None:
    primary = _FakeProvider("primary", pre_stream_error=LLMAuthError("a", provider="primary"))
    backup = _FakeProvider("backup", pre_stream_error=LLMRateLimitError("b", provider="backup"))

    router = LLMRouter(primary=primary, backups=[backup])

    with pytest.raises(LLMRateLimitError) as ei:
        await _collect(router.stream_chat(messages))

    assert ei.value.provider == "backup"


async def test_empty_stream_falls_over(messages: list[Message]) -> None:
    primary = _FakeProvider("primary", chunks=[])
    backup = _FakeProvider("backup", chunks=["ok"])

    router = LLMRouter(primary=primary, backups=[backup])
    chunks = await _collect(router.stream_chat(messages))

    assert chunks == ["ok"]


async def test_single_provider_with_no_backups_behaves_normally(
    messages: list[Message],
) -> None:
    only = _FakeProvider("only", chunks=["a", "b"])
    router = LLMRouter(primary=only)

    assert await _collect(router.stream_chat(messages)) == ["a", "b"]


async def test_single_provider_failure_raises_directly(
    messages: list[Message],
) -> None:
    only = _FakeProvider("only", pre_stream_error=LLMAuthError("nope", provider="only"))
    router = LLMRouter(primary=only)

    with pytest.raises(LLMAuthError):
        await _collect(router.stream_chat(messages))


def test_rejects_non_positive_budget() -> None:
    only = _FakeProvider("only", chunks=["a"])
    with pytest.raises(ValueError, match="must be positive"):
        LLMRouter(primary=only, first_byte_budget_s=0.0)


def test_router_is_an_llm_provider() -> None:
    only = _FakeProvider("only", chunks=["a"])
    router: LLMProvider = LLMRouter(primary=only)
    assert isinstance(router, LLMProvider)
    assert router.name == "router"


async def test_failover_event_is_logged(
    messages: list[Message], capsys: pytest.CaptureFixture[str]
) -> None:
    # The app configures structlog with a JSON renderer that writes to
    # stdout; capsys is the simplest way to assert the event without
    # fighting global structlog state shared across tests.
    primary = _FakeProvider("primary", pre_stream_error=LLMAuthError("401", provider="primary"))
    backup = _FakeProvider("backup", chunks=["ok"])
    router = LLMRouter(primary=primary, backups=[backup])

    chunks = await _collect(router.stream_chat(messages))

    assert chunks == ["ok"]
    captured = capsys.readouterr()
    assert "llm_failover" in captured.out
    assert "primary" in captured.out
