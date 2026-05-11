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
