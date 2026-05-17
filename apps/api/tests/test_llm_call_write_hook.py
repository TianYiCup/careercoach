"""A-40: TurnTrace.record_generation -> llm_calls.insert wiring.

These tests pin the four-condition gate (`usage` + `_persist_call` +
`user_id` + `surface` + `trace_id`) on the fire-and-forget DB-insert
path, and the silent-on-failure contract that keeps observability
errors from leaking into the user-facing SSE stream.

The persist callback is an `InMemoryLLMCallRepository.insert` bound
method so each test owns its own repo instance — no `lru_cache`
pollution across the suite.
"""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import MagicMock

import pytest
from app.llm.types import TokenUsage
from app.observability.langfuse import (
    TurnTrace,
    begin_copilot_trace,
    begin_review_trace,
    begin_turn_trace,
)
from app.services.llm_calls import (
    InMemoryLLMCallRepository,
    LLMCallRecord,
)


def _usage(prompt: int = 100, completion: int = 50) -> TokenUsage:
    return TokenUsage(
        prompt_tokens=prompt,
        completion_tokens=completion,
        total_tokens=prompt + completion,
    )


async def _drain() -> None:
    """Let any `loop.create_task(...)` scheduled by record_generation
    actually run. One `sleep(0)` yields control once which is enough
    for the simple _safe_persist coroutine to complete in test."""
    await asyncio.sleep(0)


# --- the happy path ---


async def test_record_generation_persists_record_when_all_conditions_met() -> None:
    repo = InMemoryLLMCallRepository()
    trace = TurnTrace(
        _trace=None,
        trace_id="trace_abc",
        user_id="u_demo",
        surface="sandbox",
        _persist_call=repo.insert,
    )

    trace.record_generation(
        name="roleplay",
        model="deepseek-chat",
        input=[],
        output="hello",
        usage=_usage(prompt=120, completion=30),
    )
    await _drain()

    agg = await repo.aggregate_by_user("u_demo")
    assert agg.totals.call_count == 1
    assert agg.totals.prompt_tokens == 120
    assert agg.totals.completion_tokens == 30
    assert agg.totals.total_tokens == 150
    assert agg.by_model[0].key == "deepseek-chat"
    assert agg.by_surface[0].key == "sandbox"


async def test_persist_runs_even_when_langfuse_trace_is_none() -> None:
    """Cost data is system-of-record we control — Langfuse outage or
    dev-mode no-op must NOT also blind the rollup endpoint."""
    repo = InMemoryLLMCallRepository()
    trace = TurnTrace(
        _trace=None,
        trace_id="trace_no_lf",
        user_id="u_dev",
        surface="copilot",
        _persist_call=repo.insert,
    )

    trace.record_generation(
        name="hint",
        model="qwen-max",
        input={},
        output="",
        usage=_usage(),
    )
    await _drain()

    agg = await repo.aggregate_by_user("u_dev")
    assert agg.totals.call_count == 1


# --- the four conditions: missing any → no persist ---


@pytest.mark.parametrize(
    ("user_id", "surface", "trace_id", "usage_obj"),
    [
        pytest.param(None, "sandbox", "trace_x", _usage(), id="no_user_id"),
        pytest.param("u_demo", None, "trace_x", _usage(), id="no_surface"),
        pytest.param("u_demo", "sandbox", None, _usage(), id="no_trace_id"),
        pytest.param("u_demo", "sandbox", "trace_x", None, id="no_usage"),
    ],
)
async def test_record_generation_skips_persist_when_any_condition_missing(
    user_id: str | None,
    surface: str | None,
    trace_id: str | None,
    usage_obj: TokenUsage | None,
) -> None:
    repo = InMemoryLLMCallRepository()
    trace = TurnTrace(
        _trace=None,
        trace_id=trace_id,
        user_id=user_id,
        surface=surface,
        _persist_call=repo.insert,
    )

    trace.record_generation(
        name="any",
        model="any-model",
        input={},
        output={},
        usage=usage_obj,
    )
    await _drain()

    agg = await repo.aggregate_by_user(user_id or "u_demo")
    assert agg.totals.call_count == 0


async def test_record_generation_skips_persist_when_persist_call_is_none() -> None:
    """A trace opened without persistence (e.g. legacy callsite that
    pre-dates A-40) must keep working — no AttributeError, no log
    spam, just silent skip on the persist side."""
    repo = InMemoryLLMCallRepository()
    trace = TurnTrace(
        _trace=None,
        trace_id="trace_legacy",
        user_id="u_demo",
        surface="sandbox",
        _persist_call=None,
    )

    trace.record_generation(name="legacy", model="m", input={}, output={}, usage=_usage())
    await _drain()

    agg = await repo.aggregate_by_user("u_demo")
    assert agg.totals.call_count == 0


# --- failure isolation ---


async def test_persist_exception_does_not_propagate_to_caller() -> None:
    """Observability failures must NEVER take down the user-facing
    flow — matches the langfuse-side swallow pattern that's been in
    place since A-21."""

    async def boom(_record: LLMCallRecord) -> None:
        raise RuntimeError("simulated DB outage")

    trace = TurnTrace(
        _trace=None,
        trace_id="trace_boom",
        user_id="u_demo",
        surface="sandbox",
        _persist_call=boom,
    )

    # The call itself returns synchronously without raising.
    trace.record_generation(name="x", model="m", input={}, output={}, usage=_usage())
    # Draining the background task must also not raise — the
    # `_safe_persist` wrapper logs the exception instead.
    await _drain()


async def test_record_generation_still_calls_langfuse_when_persist_fails() -> None:
    """Langfuse-side recording and DB-side persistence are
    independent: a persist exception must not skip the generation
    span (Langfuse is the human-debuggable observability surface;
    losing a span there would erase the only trail an oncall has)."""

    inner_trace = MagicMock(name="trace")
    inner_gen = MagicMock(name="generation")
    inner_trace.generation.return_value = inner_gen

    async def boom(_record: LLMCallRecord) -> None:
        raise RuntimeError("oops")

    trace = TurnTrace(
        _trace=inner_trace,
        trace_id="trace_id",
        user_id="u_demo",
        surface="sandbox",
        _persist_call=boom,
    )

    trace.record_generation(name="r", model="m", input={}, output="o", usage=_usage())
    await _drain()

    # The langfuse side ran regardless of the persist explosion.
    inner_trace.generation.assert_called_once()
    inner_gen.end.assert_called_once()


# --- begin_*_trace surface hardcoding ---


async def test_begin_turn_trace_hardcodes_sandbox_surface() -> None:
    repo = InMemoryLLMCallRepository()
    trace = begin_turn_trace(
        None,
        input={},
        trace_id="trace_sandbox",
        user_id="u_demo",
        persist_call=repo.insert,
    )

    trace.record_generation(name="roleplay", model="m", input={}, output={}, usage=_usage())
    await _drain()

    agg = await repo.aggregate_by_user("u_demo")
    assert agg.by_surface[0].key == "sandbox"


async def test_begin_review_trace_hardcodes_review_surface() -> None:
    repo = InMemoryLLMCallRepository()
    trace = begin_review_trace(
        None,
        input={},
        trace_id="trace_review",
        user_id="u_demo",
        persist_call=repo.insert,
    )

    trace.record_generation(name="analyze_review", model="m", input={}, output={}, usage=_usage())
    await _drain()

    agg = await repo.aggregate_by_user("u_demo")
    assert agg.by_surface[0].key == "review"


async def test_begin_copilot_trace_hardcodes_copilot_surface() -> None:
    repo = InMemoryLLMCallRepository()
    trace = begin_copilot_trace(
        None,
        input={},
        trace_id="trace_copilot",
        user_id="u_demo",
        persist_call=repo.insert,
    )

    trace.record_generation(name="coach_hint", model="m", input={}, output={}, usage=_usage())
    await _drain()

    agg = await repo.aggregate_by_user("u_demo")
    assert agg.by_surface[0].key == "copilot"


async def test_trace_id_stored_on_record_matches_caller_value() -> None:
    """Pins the contract that LLMCall.trace_id is the same value the
    caller passed into begin_*_trace — analysts pasting the trace_id
    into the Langfuse UI must find the matching trace, not a derived
    one."""
    repo = InMemoryLLMCallRepository()
    trace = begin_turn_trace(
        None,
        input={},
        trace_id="trace_aabb1122",
        user_id="u_alice",
        persist_call=repo.insert,
    )

    trace.record_generation(name="r", model="m", input={}, output={}, usage=_usage())
    await _drain()

    # In-memory repo exposes records via aggregate but we want the
    # raw trace_id — pull from the internal list (test-only access).
    records: list[Any] = repo._records
    assert len(records) == 1
    assert records[0].trace_id == "trace_aabb1122"
    assert records[0].user_id == "u_alice"


# --- multiple calls accumulate ---


async def test_multiple_record_generation_calls_aggregate_correctly() -> None:
    """Sanity: the 3-4 generations a single sandbox turn fires
    (roleplay, coach.three_tones, judge) all land as separate rows
    so the rollup can break down by `name` / `model` if a future
    column is added."""
    repo = InMemoryLLMCallRepository()
    trace = TurnTrace(
        _trace=None,
        trace_id="trace_multi",
        user_id="u_demo",
        surface="sandbox",
        _persist_call=repo.insert,
    )

    for usage in (
        _usage(prompt=200, completion=100),
        _usage(prompt=50, completion=25),
        _usage(prompt=80, completion=40),
    ):
        trace.record_generation(name="x", model="deepseek-chat", input={}, output={}, usage=usage)
    await _drain()

    agg = await repo.aggregate_by_user("u_demo")
    assert agg.totals.call_count == 3
    assert agg.totals.prompt_tokens == 330
    assert agg.totals.completion_tokens == 165
    assert agg.totals.total_tokens == 495
