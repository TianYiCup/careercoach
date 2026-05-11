"""Langfuse trace emission for session turns.

Why not the langchain CallbackHandler?
--------------------------------------
`langfuse.callback.CallbackHandler` requires the full `langchain`
package (not just `langchain-core`, which we already get via
LangGraph). That would add ~30MB of deps for surface we never use,
so we wire the lower-level `Langfuse` client directly: one trace per
session turn with input + output recorded.

Per-LLM-call generation events can be layered into the adapter or
into individual nodes later without changing this module's API.

Dev no-op contract
------------------
When `LANGFUSE_PUBLIC_KEY` or `LANGFUSE_SECRET_KEY` is empty,
`get_langfuse_client` returns None. `run_session_turn` accepts None
and degrades to a plain `graph.ainvoke(state)` so a developer who
hasn't started Langfuse locally still has a working app.
"""

from __future__ import annotations

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
