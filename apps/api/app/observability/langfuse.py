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

import asyncio
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, cast

import structlog
from langfuse import Langfuse

from app.config import get_settings
from app.llm.types import TokenUsage
from app.services.llm_calls import LLMCallRecord, get_llm_call_repository

if TYPE_CHECKING:
    from app.agents import SessionState

logger = structlog.get_logger(__name__)

# Persist callback signature: each `record_generation` with token usage
# schedules a fire-and-forget call into this. Kept narrow (one arg,
# returns awaitable) so test doubles can be a plain async lambda.
LLMCallPersistCall = Callable[[LLMCallRecord], Awaitable[None]]

# Module-level strong-ref set for fire-and-forget persist tasks.
# `loop.create_task(...)` only weak-refs the task — without a strong
# reference Python may garbage-collect mid-flight, silently dropping
# the DB write. We add on schedule + remove on done so the set's size
# tracks live in-flight inserts (typically 0 to a few even at peak).
_background_persist_tasks: set[asyncio.Task[None]] = set()


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

    A-40 added the per-trace persistence fields:

    * `trace_id` — request-id correlator (also stored in metadata).
      Used as the `llm_calls.trace_id` FK back to the Langfuse trace.
    * `user_id` — who paid for the LLM spend on this trace.
    * `surface` — which subsystem opened the trace (sandbox / review
      / copilot / agent). Hardcoded per `begin_*_trace` so callers
      don't have to remember.
    * `_persist_call` — async callable that takes one `LLMCallRecord`
      and inserts it. None disables persistence (matches the no-op
      langfuse client contract).

    When `_persist_call` is set AND `record_generation` has a usage
    object AND all of (trace_id, user_id, surface) are present,
    `record_generation` schedules a fire-and-forget task that calls
    `_persist_call(record)`. Observability failures here are caught
    and logged — they never propagate up to the user-facing flow.
    """

    _trace: Any | None
    _tags: list[str] = field(default_factory=list)
    trace_id: str | None = None
    user_id: str | None = None
    surface: str | None = None
    _persist_call: LLMCallPersistCall | None = None

    def record_generation(
        self,
        *,
        name: str,
        model: str,
        input: Any,
        output: Any,
        metadata: dict[str, Any] | None = None,
        usage: TokenUsage | None = None,
    ) -> None:
        """Record one LLM call as a `generation` under this trace.

        Generations show up on the Langfuse UI as a span with its own
        latency, model, and token-count breakdown. When `usage` is
        supplied (A-27), it's forwarded as the Langfuse generation's
        `usage` field — `{input, output, total}` — which powers the
        per-trace cost view on the Langfuse dashboard. Pass `None`
        when the provider didn't surface token counts (e.g. a stub
        in tests, or an upstream that refused `include_usage`).

        A-40: when a `usage` is supplied AND the trace was opened with
        a `_persist_call` (plus trace_id / user_id / surface), the
        same call also schedules a fire-and-forget DB insert so the
        per-user cost rollup endpoint (A-42) doesn't have to
        round-trip Langfuse on every read. Langfuse remains the
        authoritative observability store; the DB row is the queryable
        replica we control.
        """
        if self._trace is not None:
            try:
                gen_kwargs: dict[str, Any] = {
                    "name": name,
                    "model": model,
                    "input": input,
                    "metadata": metadata or {},
                }
                if usage is not None:
                    gen_kwargs["usage"] = usage.to_langfuse_usage()
                gen = self._trace.generation(**gen_kwargs)
                gen.end(output=output)
            except Exception:
                # An observability failure must NEVER take down the SSE
                # stream the user is watching. Swallow + log.
                logger.exception("langfuse_generation_failed", generation_name=name)
        # Persist runs regardless of whether the langfuse client was
        # configured — A-39's table is the system-of-record for cost
        # data, independent of Langfuse availability. The persist
        # callable itself defaults to `None` (no-op) when the trace
        # wasn't opened with persistence wired, so dev runs without
        # the wiring still see no DB write.
        self._maybe_schedule_persist(model=model, usage=usage)

    def _maybe_schedule_persist(self, *, model: str, usage: TokenUsage | None) -> None:
        """Fire-and-forget DB insert when all four conditions hold:
          * usage was surfaced by the provider
          * a persist callable was wired at trace-open time
          * the trace knows its user_id, trace_id, and surface

        If any condition is missing this is a silent no-op — matches
        the no-op-on-None pattern already in `_trace is None` above.

        We schedule with `loop.create_task` rather than `await` so
        the surrounding SSE stream isn't blocked on a DB round-trip;
        observability writes must never extend user-perceived latency.
        Missing running loop (e.g. a sync test that constructs a
        TurnTrace and calls record_generation outside of asyncio) is
        also a silent no-op — the test author already knows the
        persistence side isn't exercised under that harness.
        """
        if usage is None or self._persist_call is None:
            return
        if not self.user_id or not self.surface or not self.trace_id:
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        record = LLMCallRecord(
            id=uuid.uuid4(),
            trace_id=self.trace_id,
            user_id=self.user_id,
            surface=self.surface,
            model=model,
            prompt_tokens=usage.prompt_tokens,
            completion_tokens=usage.completion_tokens,
            total_tokens=usage.total_tokens,
            created_at=datetime.now(UTC),
        )
        task = loop.create_task(_safe_persist(self._persist_call, record))
        _background_persist_tasks.add(task)
        task.add_done_callback(_background_persist_tasks.discard)

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


async def _safe_persist(call: LLMCallPersistCall, record: LLMCallRecord) -> None:
    """Wrap one persist call so a DB failure is logged, not raised.

    Called by `TurnTrace._maybe_schedule_persist` via `loop.create_task`,
    so any exception escaping here would surface as an unawaited-task
    warning at best and a crashed event-loop callback at worst.
    Mirrors the swallow-and-log pattern in `record_generation` for
    Langfuse failures."""
    try:
        await call(record)
    except Exception:
        logger.exception(
            "llm_call_persist_failed",
            trace_id=record.trace_id,
            model=record.model,
            user_id=record.user_id,
        )


def begin_turn_trace(
    client: Langfuse | None,
    *,
    input: dict[str, Any],
    metadata: dict[str, Any] | None = None,
    session_id: str | None = None,
    tags: list[str] | None = None,
    trace_id: str | None = None,
    user_id: str | None = None,
    persist_call: LLMCallPersistCall | None = None,
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
        trace_id=trace_id,
        user_id=user_id,
        surface="sandbox",
        persist_call=persist_call or _default_persist_call(),
    )


def begin_review_trace(
    client: Langfuse | None,
    *,
    input: dict[str, Any],
    metadata: dict[str, Any] | None = None,
    session_id: str | None = None,
    tags: list[str] | None = None,
    trace_id: str | None = None,
    user_id: str | None = None,
    persist_call: LLMCallPersistCall | None = None,
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
        trace_id=trace_id,
        user_id=user_id,
        surface="review",
        persist_call=persist_call or _default_persist_call(),
    )


def begin_copilot_trace(
    client: Langfuse | None,
    *,
    input: dict[str, Any],
    metadata: dict[str, Any] | None = None,
    session_id: str | None = None,
    tags: list[str] | None = None,
    trace_id: str | None = None,
    user_id: str | None = None,
    persist_call: LLMCallPersistCall | None = None,
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
        trace_id=trace_id,
        user_id=user_id,
        surface="copilot",
        persist_call=persist_call or _default_persist_call(),
    )


def _default_persist_call() -> LLMCallPersistCall | None:
    """Look up the configured llm_call repo and return its `insert`
    bound method, or `None` on any wiring error.

    Pulled into a helper so each `begin_*_trace` stays a one-liner and
    so tests that want to bypass persistence can pass an explicit
    `persist_call=` instead of clearing the `lru_cache` on
    `get_llm_call_repository`. We swallow exceptions here because an
    observability-side wiring failure must not stop a user-facing
    trace from being opened — the trace just runs without persistence
    (same posture as a no-op Langfuse client).
    """
    try:
        return get_llm_call_repository().insert
    except Exception:
        logger.exception("llm_call_repository_unavailable")
        return None


def _begin_named_trace(
    client: Langfuse | None,
    *,
    name: str,
    input: dict[str, Any],
    metadata: dict[str, Any] | None,
    session_id: str | None = None,
    tags: list[str] | None = None,
    trace_id: str | None = None,
    user_id: str | None = None,
    surface: str | None = None,
    persist_call: LLMCallPersistCall | None = None,
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
        # Persistence runs even in the no-op-langfuse case so the cost
        # table stays accurate when the dev env has Langfuse disabled.
        return TurnTrace(
            _trace=None,
            trace_id=trace_id,
            user_id=user_id,
            surface=surface,
            _persist_call=persist_call,
        )
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
        # Persistence still wires through on Langfuse-create failure
        # so a Langfuse outage doesn't also blind the cost rollup.
        return TurnTrace(
            _trace=None,
            trace_id=trace_id,
            user_id=user_id,
            surface=surface,
            _persist_call=persist_call,
        )
    return TurnTrace(
        _trace=trace,
        _tags=initial_tags,
        trace_id=trace_id,
        user_id=user_id,
        surface=surface,
        _persist_call=persist_call,
    )


__all__ = [
    "TurnTrace",
    "begin_copilot_trace",
    "begin_review_trace",
    "begin_turn_trace",
    "get_langfuse_client",
    "run_session_turn",
]
