"""Tests for the Langfuse trace integration.

We never hit a real Langfuse instance — `Langfuse` is patched out
so the tests assert on the calls our code makes, not on the SDK's
network behaviour.
"""

from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from app.config import get_settings
from app.observability.langfuse import (
    get_langfuse_client,
    run_session_turn,
)
from pydantic import SecretStr


@pytest.fixture(autouse=True)
def _clear_settings_cache() -> None:
    # Settings are cached for the process; per-test mutation needs
    # the cache reset so monkeypatched fields are visible.
    get_settings.cache_clear()


def _set_keys(monkeypatch: pytest.MonkeyPatch, *, public: str, secret: str) -> None:
    monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", public)
    monkeypatch.setenv("LANGFUSE_SECRET_KEY", secret)


def test_get_client_returns_none_when_both_keys_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _set_keys(monkeypatch, public="", secret="")
    assert get_langfuse_client() is None


def test_get_client_returns_none_when_only_public_set(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _set_keys(monkeypatch, public="pk_x", secret="")
    assert get_langfuse_client() is None


def test_get_client_returns_none_when_only_secret_set(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _set_keys(monkeypatch, public="", secret="sk_x")
    assert get_langfuse_client() is None


def test_get_client_constructs_with_settings_when_keys_present(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _set_keys(monkeypatch, public="pk_x", secret="sk_x")
    monkeypatch.setenv("LANGFUSE_HOST", "https://langfuse.example.test")

    with patch("app.observability.langfuse.Langfuse") as ctor:
        ctor.return_value = MagicMock(name="client")
        client = get_langfuse_client()

    ctor.assert_called_once_with(
        public_key="pk_x",
        secret_key="sk_x",
        host="https://langfuse.example.test",
    )
    assert client is ctor.return_value


def test_get_client_passes_secret_values_not_secretstr(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Defence against accidentally passing SecretStr objects through —
    # the SDK would str() them and trace events would carry literal
    # "**********" strings instead of the real key.
    _set_keys(monkeypatch, public="pk_real", secret="sk_real")

    with patch("app.observability.langfuse.Langfuse") as ctor:
        get_langfuse_client()

    _args, kwargs = ctor.call_args
    assert isinstance(kwargs["public_key"], str)
    assert not isinstance(kwargs["public_key"], SecretStr)
    assert kwargs["public_key"] == "pk_real"
    assert kwargs["secret_key"] == "sk_real"


class _FakeGraph:
    """Stand-in for the compiled LangGraph."""

    def __init__(self, *, result: dict[str, Any] | Exception) -> None:
        self._result = result
        self.invocations: list[dict[str, Any]] = []

    async def ainvoke(self, state: Any) -> Any:
        self.invocations.append(state)
        if isinstance(self._result, Exception):
            raise self._result
        return self._result


async def test_run_session_turn_without_client_just_runs_graph() -> None:
    graph = _FakeGraph(result={"opponent_reply": "ok"})
    state = {"user_turn": "hi"}

    result = await run_session_turn(graph=graph, state=state, client=None)  # type: ignore[arg-type]

    assert result == {"opponent_reply": "ok"}
    assert graph.invocations == [state]


async def test_run_session_turn_creates_trace_with_input_and_output() -> None:
    client = MagicMock(name="langfuse")
    trace = MagicMock(name="trace")
    client.trace.return_value = trace

    graph = _FakeGraph(result={"opponent_reply": "done"})
    state = {"user_turn": "hi"}

    result = await run_session_turn(
        graph=graph,
        state=state,  # type: ignore[arg-type]
        client=client,
        trace_name="t1",
        metadata={"trace_id": "abc"},
    )

    assert result == {"opponent_reply": "done"}
    client.trace.assert_called_once_with(
        name="t1",
        input=state,
        metadata={"trace_id": "abc"},
    )
    trace.update.assert_called_once_with(output={"opponent_reply": "done"})


async def test_run_session_turn_marks_trace_error_and_reraises() -> None:
    client = MagicMock(name="langfuse")
    trace = MagicMock(name="trace")
    client.trace.return_value = trace

    boom = RuntimeError("kaboom")
    graph = _FakeGraph(result=boom)
    state = {"user_turn": "hi"}

    with pytest.raises(RuntimeError, match="kaboom"):
        await run_session_turn(
            graph=graph,
            state=state,  # type: ignore[arg-type]
            client=client,
        )

    trace.update.assert_called_once_with(level="ERROR", status_message="kaboom")


async def test_run_session_turn_default_metadata_is_empty_dict() -> None:
    client = MagicMock(name="langfuse")
    client.trace.return_value = MagicMock(name="trace")

    graph = _FakeGraph(result={})
    await run_session_turn(graph=graph, state={}, client=client)

    _args, kwargs = client.trace.call_args
    assert kwargs["metadata"] == {}


# ---------------------------------------------------------------------
# `TurnTrace` / `begin_turn_trace` — the SSE-side wrapper used by
# `TurnService.stream_turn`.
# ---------------------------------------------------------------------


def test_begin_turn_trace_returns_noop_when_client_is_none() -> None:
    """No Langfuse keys → no trace handle. Caller doesn't have to
    branch — every method on the returned wrapper short-circuits."""
    from app.observability.langfuse import begin_turn_trace

    trace = begin_turn_trace(None, input={"x": 1}, metadata={"y": 2})

    # No raises — every method is safe to call.
    trace.record_generation(name="g", model="m", input={}, output="ok")
    trace.finish(output={"done": True})
    trace.fail(RuntimeError("boom"))


def test_begin_turn_trace_passes_input_and_metadata_to_client() -> None:
    from app.observability.langfuse import begin_turn_trace

    client = MagicMock(name="langfuse")
    client.trace.return_value = MagicMock(name="trace")

    begin_turn_trace(
        client,
        input={"session_id": "ses_x"},
        metadata={"user_id": "u1", "trace_id": "t1"},
    )

    client.trace.assert_called_once_with(
        name="session_turn",
        input={"session_id": "ses_x"},
        metadata={"user_id": "u1", "trace_id": "t1"},
    )


def test_record_generation_calls_trace_generation_and_ends_it() -> None:
    from app.observability.langfuse import begin_turn_trace

    client = MagicMock(name="langfuse")
    inner_trace = MagicMock(name="trace")
    inner_gen = MagicMock(name="generation")
    inner_trace.generation.return_value = inner_gen
    client.trace.return_value = inner_trace

    trace = begin_turn_trace(client, input={}, metadata={})
    trace.record_generation(
        name="roleplay",
        model="router",
        input=[{"role": "user", "content": "hi"}],
        output="reply",
    )

    inner_trace.generation.assert_called_once_with(
        name="roleplay",
        model="router",
        input=[{"role": "user", "content": "hi"}],
        metadata={},
    )
    inner_gen.end.assert_called_once_with(output="reply")


def test_finish_calls_update_with_output() -> None:
    from app.observability.langfuse import begin_turn_trace

    client = MagicMock(name="langfuse")
    inner_trace = MagicMock(name="trace")
    client.trace.return_value = inner_trace

    trace = begin_turn_trace(client, input={}, metadata={})
    trace.finish(output={"verdict": "shenfeng"})

    inner_trace.update.assert_called_once_with(output={"verdict": "shenfeng"})


def test_fail_calls_update_with_error_level() -> None:
    from app.observability.langfuse import begin_turn_trace

    client = MagicMock(name="langfuse")
    inner_trace = MagicMock(name="trace")
    client.trace.return_value = inner_trace

    trace = begin_turn_trace(client, input={}, metadata={})
    trace.fail(RuntimeError("kaboom"))

    inner_trace.update.assert_called_once_with(level="ERROR", status_message="kaboom")


def test_trace_create_failure_degrades_to_noop() -> None:
    """If `client.trace(...)` itself raises (network / SDK bug), the
    caller must still get a usable no-op trace so the SSE stream
    isn't broken by an observability failure."""
    from app.observability.langfuse import begin_turn_trace

    client = MagicMock(name="langfuse")
    client.trace.side_effect = RuntimeError("langfuse down")

    trace = begin_turn_trace(client, input={}, metadata={})

    # No raise. All subsequent calls also no-op.
    trace.record_generation(name="g", model="m", input={}, output="ok")
    trace.finish(output={})
    trace.fail(RuntimeError("x"))


def test_record_generation_swallows_underlying_failure() -> None:
    """The same fail-open story for sub-calls: observability errors
    must never propagate out and abort the SSE pipeline."""
    from app.observability.langfuse import begin_turn_trace

    client = MagicMock(name="langfuse")
    inner_trace = MagicMock(name="trace")
    inner_trace.generation.side_effect = RuntimeError("flush failed")
    client.trace.return_value = inner_trace

    trace = begin_turn_trace(client, input={}, metadata={})
    trace.record_generation(name="g", model="m", input={}, output="ok")  # no raise


# ---------------------------------------------------------------------
# `begin_copilot_trace` — the WS-side wrapper used per utterance by
# the copilot stream handler (A-21).
# ---------------------------------------------------------------------


def test_begin_copilot_trace_returns_noop_when_client_is_none() -> None:
    """Same dev-no-op contract as the other entry points: no Langfuse
    keys → every method on the returned wrapper short-circuits."""
    from app.observability.langfuse import begin_copilot_trace

    trace = begin_copilot_trace(
        None,
        input={"scenario_hint": "面试"},
        metadata={"copilot_id": "cop_x"},
    )

    # No raises — every method is safe to call.
    trace.record_generation(name="transcribe", model="dummy", input={}, output={})
    trace.finish(output={"final_text": "", "verdict": None})
    trace.fail(RuntimeError("boom"))


def test_begin_copilot_trace_uses_copilot_utterance_name() -> None:
    """Trace name is what analysts filter on in the Langfuse UI;
    pin the literal value so a rename doesn't silently break the
    saved-views layer."""
    from app.observability.langfuse import begin_copilot_trace

    client = MagicMock(name="langfuse")
    client.trace.return_value = MagicMock(name="trace")

    begin_copilot_trace(
        client,
        input={"scenario_hint": "interview"},
        metadata={"copilot_id": "cop_x", "user_id": "u_1"},
    )

    client.trace.assert_called_once_with(
        name="copilot_utterance",
        input={"scenario_hint": "interview"},
        metadata={"copilot_id": "cop_x", "user_id": "u_1"},
    )


# ---------------------------------------------------------------------
# A-23 — `session_id` pass-through to the Langfuse top-level session
# field so analysts can use the session-grouping UI.
# ---------------------------------------------------------------------


def test_session_id_pass_through_for_copilot_trace() -> None:
    """A-23: `session_id` lifts copilot_id into Langfuse's top-level
    session field. Confirms the kwarg actually reaches `client.trace`
    (not just stuffed in metadata)."""
    from app.observability.langfuse import begin_copilot_trace

    client = MagicMock(name="langfuse")
    client.trace.return_value = MagicMock(name="trace")

    begin_copilot_trace(
        client,
        input={"scenario_hint": "interview"},
        metadata={"user_id": "u_1"},
        session_id="cop_x",
    )

    _, kwargs = client.trace.call_args
    assert kwargs["session_id"] == "cop_x"


def test_session_id_pass_through_for_turn_trace() -> None:
    """Same pass-through for sandbox turns — session_id = sandbox session id."""
    from app.observability.langfuse import begin_turn_trace

    client = MagicMock(name="langfuse")
    client.trace.return_value = MagicMock(name="trace")

    begin_turn_trace(
        client,
        input={"session_id": "ses_x", "user_content": "hi"},
        metadata={"user_id": "u_1"},
        session_id="ses_x",
    )

    _, kwargs = client.trace.call_args
    assert kwargs["session_id"] == "ses_x"


def test_session_id_pass_through_for_review_trace() -> None:
    """Review traces use upload_id as the session id so each upload
    is one one-trace session in the grouping UI."""
    from app.observability.langfuse import begin_review_trace

    client = MagicMock(name="langfuse")
    client.trace.return_value = MagicMock(name="trace")

    begin_review_trace(
        client,
        input={"upload_id": "up_x"},
        metadata={"user_id": "u_1"},
        session_id="up_x",
    )

    _, kwargs = client.trace.call_args
    assert kwargs["session_id"] == "up_x"


def test_session_id_omitted_when_not_provided() -> None:
    """No session_id arg → no `session_id` kwarg on the underlying
    `client.trace(...)` call. Keeps legacy call sites' behavior
    identical and avoids burning a None on every trace."""
    from app.observability.langfuse import begin_turn_trace

    client = MagicMock(name="langfuse")
    client.trace.return_value = MagicMock(name="trace")

    begin_turn_trace(client, input={"x": 1}, metadata={"y": 2})

    _, kwargs = client.trace.call_args
    assert "session_id" not in kwargs
