"""Langfuse trace emission for session turns and review uploads.

Why not the langchain CallbackHandler?
--------------------------------------
`langfuse.callback.CallbackHandler` requires the full `langchain`
package (not just `langchain-core`, which we already get via
LangGraph). That would add ~30MB of deps for surface we never use,
so we wire the lower-level `Langfuse` client directly.

Entry points:
  * `run_session_turn` wraps a LangGraph `ainvoke` (unused in
    production today — `/turns` drives the LLM directly — kept for
    the future cycle/checkpoint work).
  * `TurnTrace` is the shared trace wrapper. Despite the name (kept
    for backwards-compat across the existing `TurnService` import
    site) it's generic: the class only wraps a trace handle and
    exposes `record_generation` / `finish` / `fail`. A future cleanup
    PR can rename it to `NullableTrace` or similar.
  * `begin_turn_trace(...)` opens a `session_turn`-named trace; the
    `/turns` SSE pipeline uses it.
  * `begin_review_trace(...)` opens a `review_upload`-named trace;
    the review-mode background worker (A-13) uses it.
  * `begin_copilot_trace(...)` opens a `copilot_utterance`-named
    trace; the copilot WS handler (A-21) opens one per utterance and
    attaches `transcribe` / `moderate` / `coach_hint` generations.

Dev no-op contract
------------------
When `LANGFUSE_PUBLIC_KEY` or `LANGFUSE_SECRET_KEY` is empty,
`get_langfuse_client` returns None. Every helper here accepts a
`None` client and degrades to no-op so a developer who hasn't
started Langfuse locally still has a working app.
"""

from __future__ import annotations

from dataclasses import dataclass, field
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
    `record_generation` / `finish` / `fail` / `add_tags` unconditionally
    — null checks live here once, not at every LLM call site. When the
    underlying `_trace` is `None`, every method short-circuits.

    `_trace` is typed `Any` because langfuse v2's `StatefulTraceClient`
    isn't part of its public namespace; tests patch in a `MagicMock`
    of the same shape.

    `_tags` is the cumulative tag list (A-24). Stored in the wrapper
    because Langfuse v2's `trace.update(tags=...)` REPLACES rather
    than appends, so to add a tag mid-trace we have to re-send the
    full union. `field(default_factory=list)` keeps the dataclass
    frozen while still allowing the *contents* of the list to mutate.
    """

    _trace: Any | None
    _tags: list[str] = field(default_factory=list)

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

    def add_tags(self, tags: list[str]) -> None:
        """Append `tags` to the trace's tag list (A-24).

        Langfuse v2's `update(tags=...)` REPLACES — so we keep the
        cumulative list in `_tags` and re-send the full union on each
        add. Duplicates are de-duped while preserving insertion order
        (Python 3.7+ dict ordering) so the Langfuse UI shows a stable
        tag list even when callers double-tag (e.g. a verdict-tag added
        twice after a re-moderation).

        No-op when `_trace` is None or `tags` is empty.
        """
        if self._trace is None or not tags:
            return
        for tag in tags:
            if tag not in self._tags:
                self._tags.append(tag)
        try:
            self._trace.update(tags=list(self._tags))
        except Exception:
            logger.exception("langfuse_trace_tag_update_failed", tag_count=len(tags))


def begin_turn_trace(
    client: Langfuse | None,
    *,
    input: dict[str, Any],
    metadata: dict[str, Any] | None = None,
    session_id: str | None = None,
    tags: list[str] | None = None,
) -> TurnTrace:
    """Start a Langfuse trace for one `/turns` SSE call.

    `session_id` is promoted to Langfuse's top-level session field
    (A-23) so analysts can use Langfuse's session-grouping UI to see
    all traces for a given sandbox session. Callers should pass the
    sandbox `session_id`. None disables grouping.

    `tags` is the initial tag set (A-24) for cross-cutting filtering
    on the Langfuse UI (e.g. `surface:sandbox`, `minor:true`). Tags
    can be added mid-trace via `TurnTrace.add_tags` once outcomes
    like moderation verdict are known.

    Returns a `TurnTrace` whose methods are no-ops when `client` is
    `None`, so the caller doesn't need to branch on dev vs. prod.
    """
    return _begin_named_trace(
        client,
        name="session_turn",
        input=input,
        metadata=metadata,
        session_id=session_id,
        tags=tags,
    )


def begin_review_trace(
    client: Langfuse | None,
    *,
    input: dict[str, Any],
    metadata: dict[str, Any] | None = None,
    session_id: str | None = None,
    tags: list[str] | None = None,
) -> TurnTrace:
    """Start a Langfuse trace for one review upload analysis.

    `session_id` is promoted to Langfuse's top-level session field
    (A-23). For review, callers should pass the `upload_id` so each
    upload is its own one-trace session — that keeps the grouping UI
    useful even though review is currently single-shot.

    Used by `ReviewService._process_upload` (A-14). Same no-op-on-None
    contract as `begin_turn_trace`; the only difference is the trace
    name surfaced on the Langfuse UI, which lets analysts filter
    review traces away from sandbox `/turns` traces.
    """
    return _begin_named_trace(
        client,
        name="review_upload",
        input=input,
        metadata=metadata,
        session_id=session_id,
        tags=tags,
    )


def begin_copilot_trace(
    client: Langfuse | None,
    *,
    input: dict[str, Any],
    metadata: dict[str, Any] | None = None,
    session_id: str | None = None,
    tags: list[str] | None = None,
) -> TurnTrace:
    """Start a Langfuse trace for one copilot WS utterance.

    `session_id` is promoted to Langfuse's top-level session field
    (A-23). Callers should pass the `copilot_id` so every utterance
    in one WS connection lands under the same Langfuse session row,
    which is the primary win — analysts can scroll through a single
    user's full live-coaching arc instead of stitching utterances
    together by metadata filtering.

    Used by the copilot WS handler (A-21). The handler opens one trace
    per `audio_end` and attaches `transcribe` / `moderate` /
    `coach_hint` generations as the utterance progresses. A unique
    trace per utterance (rather than per WS connection) keeps the
    Langfuse timeline readable even for long sessions and lets
    analysts filter on individual problematic moments.
    """
    return _begin_named_trace(
        client,
        name="copilot_utterance",
        input=input,
        metadata=metadata,
        session_id=session_id,
        tags=tags,
    )


def _begin_named_trace(
    client: Langfuse | None,
    *,
    name: str,
    input: dict[str, Any],
    metadata: dict[str, Any] | None,
    session_id: str | None = None,
    tags: list[str] | None = None,
) -> TurnTrace:
    """Internal helper — the only difference between trace entry points
    is the `name` field; everything else (null-client handling, error
    swallowing, return shape, session_id / tags pass-through) is
    identical. Keeping it in one place means a future change to that
    contract (e.g. attaching tenant ids) only touches one function.

    `session_id` and `tags` are omitted from the underlying
    `client.trace(...)` call when None / empty so we don't burn kwarg
    slots on every legacy call site that doesn't need them.

    Initial tags are also stored on the `TurnTrace` wrapper so a
    later `add_tags` call can re-send the full union (Langfuse v2's
    `update(tags=...)` REPLACES rather than appends).
    """
    if client is None:
        return TurnTrace(_trace=None)
    initial_tags = list(tags) if tags else []
    try:
        trace_kwargs: dict[str, Any] = {
            "name": name,
            "input": input,
            "metadata": metadata or {},
        }
        if session_id is not None:
            trace_kwargs["session_id"] = session_id
        if initial_tags:
            # Pass a copy so later `add_tags` mutations don't appear
            # retroactively in `client.trace`'s recorded kwargs (matters
            # for MagicMock-based tests + for the SDK's own argument
            # snapshot semantics — Langfuse v2 doesn't deep-copy
            # incoming lists in every code path).
            trace_kwargs["tags"] = list(initial_tags)
        trace = client.trace(**trace_kwargs)
    except Exception:
        logger.exception("langfuse_trace_create_failed", trace_name=name)
        return TurnTrace(_trace=None)
    return TurnTrace(_trace=trace, _tags=initial_tags)


__all__ = [
    "TurnTrace",
    "begin_copilot_trace",
    "begin_review_trace",
    "begin_turn_trace",
    "get_langfuse_client",
    "run_session_turn",
]
