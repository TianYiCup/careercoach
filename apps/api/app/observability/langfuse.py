"""Langfuse trace emission for session turns.

Why not the langchain CallbackHandler?
--------------------------------------
`langfuse.callback.CallbackHandler` requires the full `langchain`
package (not just `langchain-core`, which we already get via
LangGraph). That would add ~30MB of deps for surface we never use,
so we wire the lower-level `Langfuse` client directly.

Two entry points:
  * `run_session_turn` wraps a LangGraph `ainvoke` (unused in
    production today — `/turns` drives the LLM directly — kept for
    the future cycle/checkpoint work).
  * `TurnTrace` is the SSE-side wrapper. The `TurnService.stream_turn`
    pipeline calls `begin_turn_trace(...)` once at the top, then
    `trace.record_generation(...)` after each LLM call, and
    `trace.finish(...)` / `trace.fail(...)` at the end.

Dev no-op contract
------------------
When `LANGFUSE_PUBLIC_KEY` or `LANGFUSE_SECRET_KEY` is empty,
`get_langfuse_client` returns None. Every helper here accepts a
`None` client and degrades to no-op so a developer who hasn't
started Langfuse locally still has a working app.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, cast

import structlog
from langfuse import Langfuse

from app.config import get_settings

if TYPE_CHECKING:
    from app.agents import SessionState

logger = structlog.get_logger(__name__)


def get_langfuse_client() -> Langfuse | None:
    """Build a Langfuse client from settings, or None when disabled.

    Cached at the call site (the API layer holds one per process).
    """
    settings = get_settings()
    public_key = settings.langfuse_public_key.get_secret_value()
    secret_key = settings.langfuse_secret_key.get_secret_value()
    if not public_key or not secret_key:
        logger.debug("langfuse_disabled", reason="missing_keys")
        return None
    return Langfuse(
        public_key=public_key,
        secret_key=secret_key,
        host=settings.langfuse_host,
    )


async def run_session_turn(
    *,
    graph: Any,
    state: SessionState,
    client: Langfuse | None,
    trace_name: str = "session_turn",
    metadata: dict[str, Any] | None = None,
) -> SessionState:
    """Run the compiled LangGraph for one turn and emit one trace.

    Trace lifecycle:
      * created with `state` as input + caller `metadata`
      * on success — updated with the final state as output
      * on failure — level=ERROR + status message, then re-raises so
        the API layer still sees the exception

    `graph` is typed `Any` because LangGraph's compiled `Pregel` is
    re-exported from a few places and its public name drifts between
    minor releases; the test contract is `await graph.ainvoke(state)`.
    """
    if client is None:
        return cast("SessionState", await graph.ainvoke(state))

    trace = client.trace(
        name=trace_name,
        input=state,
        metadata=metadata or {},
    )
    try:
        result = cast("SessionState", await graph.ainvoke(state))
    except Exception as exc:
        trace.update(level="ERROR", status_message=str(exc))
        raise

    trace.update(output=result)
    return result


@dataclass(frozen=True)
class TurnTrace:
    """Wrapper around a Langfuse trace handle (or `None` for no-op mode).

    The wrapper exists so `TurnService.stream_turn` can call
    `record_generation` / `finish` / `fail` unconditionally — null
    checks live here once, not at every LLM call site. When the
    underlying `_trace` is `None`, every method short-circuits.

    `_trace` is typed `Any` because langfuse v2's `StatefulTraceClient`
    isn't part of its public namespace; tests patch in a `MagicMock`
    of the same shape.
    """

    _trace: Any | None

    def record_generation(
        self,
        *,
        name: str,
        model: str,
        input: Any,
        output: Any,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        """Record one LLM call as a `generation` under this trace.

        Generations show up on the Langfuse UI as a span with its own
        latency, model, and token-count breakdown. We don't currently
        pass token usage — the streaming `LLMProvider` interface
        doesn't surface it. Adding it later is a non-breaking change.
        """
        if self._trace is None:
            return
        try:
            gen = self._trace.generation(
                name=name,
                model=model,
                input=input,
                metadata=metadata or {},
            )
            gen.end(output=output)
        except Exception:
            # An observability failure must NEVER take down the SSE
            # stream the user is watching. Swallow + log.
            logger.exception("langfuse_generation_failed", generation_name=name)

    def finish(self, *, output: dict[str, Any]) -> None:
        """Mark the trace as completed with the given output payload."""
        if self._trace is None:
            return
        try:
            self._trace.update(output=output)
        except Exception:
            logger.exception("langfuse_trace_finish_failed")

    def fail(self, exc: BaseException) -> None:
        """Mark the trace as ERROR. Caller still re-raises the original
        exception — this is observability, not error handling."""
        if self._trace is None:
            return
        try:
            self._trace.update(level="ERROR", status_message=str(exc))
        except Exception:
            logger.exception("langfuse_trace_fail_failed")


def begin_turn_trace(
    client: Langfuse | None,
    *,
    input: dict[str, Any],
    metadata: dict[str, Any] | None = None,
) -> TurnTrace:
    """Start a Langfuse trace for one `/turns` SSE call.

    Returns a `TurnTrace` whose methods are no-ops when `client` is
    `None`, so the caller doesn't need to branch on dev vs. prod.
    """
    if client is None:
        return TurnTrace(_trace=None)
    try:
        trace = client.trace(
            name="session_turn",
            input=input,
            metadata=metadata or {},
        )
    except Exception:
        logger.exception("langfuse_trace_create_failed")
        return TurnTrace(_trace=None)
    return TurnTrace(_trace=trace)


__all__ = [
    "TurnTrace",
    "begin_turn_trace",
    "get_langfuse_client",
    "run_session_turn",
]
