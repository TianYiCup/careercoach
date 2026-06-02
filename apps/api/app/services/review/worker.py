"""Background work queue for the review-mode analyzer.

Why this layer exists
---------------------
A-11/A-12 ran the LLM analyzer + output moderation inline inside the
HTTP request. That fits within PRD §3.3 US-C1 L4's 2s budget for a
typical 5000-char input, but it leaves no slack for retries, slower
backups (Qwen via DashScope), output moderation latency, or future
multi-pass critics. A-13 (this module) lifts the post-persist work
into a background task so `POST /v1/review/uploads` returns in
~50ms with `status="processing"`; the worker flips the row to
`done` or `failed` from the side.

Queue shape
-----------
The Protocol takes a no-arg async callable (the closure the service
builds around the upload id + payload). Keeping the interface that
narrow means:
  * the implementation never has to know about review-specific types
  * tests can substitute a sync / deferred queue without faking the
    service's internal data
  * a Redis / Celery / Cloud-Tasks impl (A-14+) is a drop-in swap

In-process is fine for v0
-------------------------
v0 ships a single uvicorn worker (foundation §3.1 deploy diagram).
That makes in-process `asyncio.create_task` sufficient — the work
lives in the same event loop the route returns from, and the
process restart story is "you lose in-flight `processing` rows and
the GET shows them as still processing forever". A-14 swaps to a
Redis-backed queue once we have a real second worker or want
restart-safety; the repo contract (`update_result` is idempotent
via DELETE+INSERT) means the migration is mechanical.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import Protocol, runtime_checkable

import structlog

logger = structlog.get_logger(__name__)

# Type alias for the callable shape we enqueue: a zero-arg async function.
# Callers wrap their actual work in a closure that captures the needed
# arguments — this keeps the queue Protocol blissfully untyped about
# review payloads.
WorkFactory = Callable[[], Awaitable[None]]


@runtime_checkable
class ReviewWorkerQueue(Protocol):
    """Schedule background work for the review pipeline.

    Implementations MUST:
      * be safe to call from any number of concurrent requests
      * NEVER raise from `enqueue` for routine outcomes — a failed
        enqueue means the route should 5xx, since the row is already
        in the DB and we'd leak a `processing` upload otherwise
    """

    name: str
    """Stable identifier used in logs (e.g. "in_process")."""

    async def enqueue(self, work: WorkFactory) -> None:
        """Schedule `work` to run; return as soon as the scheduling is
        durable (for an in-process queue, that's immediately; for a
        Redis-backed queue, after the LPUSH ack)."""
        ...

    async def wait_idle(self) -> None:
        """Block until every previously-enqueued task has finished.

        Primarily for graceful shutdown + tests. A worker queue with
        no pending tasks returns immediately. For Redis-style queues
        this would be a no-op (or a polling on the work-done counter);
        for in-process it gathers the task handles.
        """
        ...


class InProcessWorkerQueue:
    """Production queue backed by `asyncio.create_task`.

    Tracks the live `Task` handles in a set so the event loop doesn't
    garbage-collect them mid-run (a well-documented `create_task`
    footgun). Tasks remove themselves via `add_done_callback` when
    they finish.

    Concurrency is unbounded — v0 traffic is too low to justify a
    semaphore, and the LLM router upstream is what would back-pressure
    a flood (rate-limited + retried + falls back to Qwen). When we
    actually see contention, a semaphore + 503 on `enqueue` lands.
    """

    name = "in_process"

    def __init__(self) -> None:
        self._tasks: set[asyncio.Task[None]] = set()

    async def enqueue(self, work: WorkFactory) -> None:
        task = asyncio.create_task(_run_with_logging(work))
        self._tasks.add(task)
        # `discard` (not `remove`) so a double-callback wouldn't raise;
        # `add_done_callback` runs after the task object holds the
        # final result, so removing here can't race with our caller.
        task.add_done_callback(self._tasks.discard)

    async def wait_idle(self) -> None:
        if not self._tasks:
            return
        # Snapshot the set: new enqueues from inside the awaited tasks
        # would otherwise mutate it during iteration.
        pending = list(self._tasks)
        await asyncio.gather(*pending, return_exceptions=True)


class SyncWorkerQueue:
    """Inline queue — runs the review analysis *synchronously* inside the
    request so `POST /v1/review/uploads` returns a terminal record
    (`done` / `failed`) rather than `processing`.

    Why this is the v0 default (not `InProcessWorkerQueue`)
    ------------------------------------------------------
    The async queue returns `processing` and relies on the client
    polling `GET /uploads/{id}` until the worker flips the row. The
    shipped web/EXE result page (`ReviewResultPage`) fetches **once**
    and never polls — so an async POST strands it on an empty
    "0 turns / NO RESULT" screen even though the analysis succeeds a
    second later. That was the reported "复盘功能异常".

    Running inline keeps the analysis within the request: the upload
    page already shows a "分析中…" pending spinner during POST, and the
    result page's single GET then sees the folded record. The LLM
    round-trip is ~1-2s (DeepSeek primary, bounded + Qwen fallback),
    well inside the request budget for v0's single-uvicorn deploy
    (foundation §3.1).

    `InProcessWorkerQueue` stays available for when the client grows a
    polling loop (and the A-14 Redis migration) — the Protocol is
    unchanged, so re-enabling async is a one-line swap in
    `get_review_worker_queue`.
    """

    name = "sync_inline"

    async def enqueue(self, work: WorkFactory) -> None:
        # Run inline, but through the same guard as the async queue so a
        # bug in the worker body NEVER raises out of `enqueue`: the
        # Protocol forbids it (the `processing` row is already persisted,
        # so a raise here would 5xx a POST and leak that row). Routine
        # failures (LLMError, output ModerationBackendError) are already
        # folded to `failed` inside `_process_upload`; only true bugs
        # reach `_run_with_logging`, which logs and swallows.
        await _run_with_logging(work)

    async def wait_idle(self) -> None:
        # Work already ran inline during `enqueue`; nothing is in flight.
        return None


async def _run_with_logging(work: WorkFactory) -> None:
    """Top-level wrapper so an uncaught exception in a background
    task is observable (asyncio prints a "Task exception was never
    retrieved" warning, but it's easy to miss in production logs).

    The service-level handlers catch every routine failure (`LLMError`,
    `ModerationBackendError` on output) before they reach here, so
    anything that propagates IS a bug — log it loud and let the task
    end. The repo row stays in `processing`; a polling client will
    eventually 504-ish on the UI side and a retry button is the
    user-facing recovery.
    """
    try:
        await work()
    except Exception:
        logger.exception("review_background_work_crashed")
