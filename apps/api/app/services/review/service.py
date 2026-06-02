"""Review-mode (复盘师) HTTP service — orchestrates analyze_review + persistence.

Why this layer exists between the route and the repo
-----------------------------------------------------
A-9 landed the persistence shell, A-10 the LLM analyzer, A-11 the
route shell, A-12 the moderation cascade, A-13 (this revision) the
async queue. The route stays HTTP plumbing; orchestration (input mod
→ persist → enqueue → return processing; worker runs analyze → output
mod → fold result → status flip) is one place so future work
(Langfuse trace, retry policy, Redis queue swap) only touches this
file.

Async flow (A-13)
-----------------
`create_upload` runs the input-side checks INLINE (input moderation
must still gate before the row is created so a blocked upload leaves
no orphan), persists the `processing` record, hands the rest off to
`self._queue`, and returns the record with `status="processing"`.

The worker side runs `_process_upload`, which:
  1. calls `analyze_review` (LLM round-trip)
  2. re-checks the LLM-generated coaching text against the moderation
     backend (defence in depth)
  3. folds the result via `update_result` OR drops it via `mark_failed`

`UpdateResult` is idempotent (DELETE+INSERT on `review_turns`), so a
crash mid-worker on a Redis-backed queue (A-14+) replays cleanly.

Moderation policy for review (unchanged from A-12)
--------------------------------------------------
INPUT moderation runs **before any persistence**. Verdict mapping
mirrors `TurnService.validate_turn_request`:
  * `block`    → raise `ReviewInputBlockedError` → route 400
  * `warn`     → proceed (minors had `warn` elevated to `block`
                 inside the service via `_apply_minor_strictness`)
  * `redirect` → proceed; `/v1/moderation/check` renders the resource
  * `allow`    → proceed

OUTPUT moderation runs inside the worker, on the concatenated
LLM-generated coaching strings. Verdict mapping is stricter — anything
but `allow` / `warn` flips the upload to `failed`. Output-side backend
errors are caught and treated as "allow" so an Aliyun outage doesn't
trash analyses the user already paid for; input-side backend errors
stay uncaught (no orphan row exists yet — 5xx is the right signal).

Ownership semantics for `get_upload`
------------------------------------
Returns None for both "no such row" and "row exists but belongs to a
different user" — the route layer maps both to 404 so a probing
client can't enumerate upload_ids by status code.
"""

from __future__ import annotations

import asyncio
import secrets
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Protocol

import structlog
from langfuse import Langfuse

from app.llm import LLMError, LLMProvider, TokenUsage
from app.observability.langfuse import TurnTrace, begin_review_trace
from app.schemas.moderation import ModerationCheckRequest
from app.services.moderation.backend import ModerationBackendError
from app.services.moderation.service import ModerationService
from app.services.review.repository import ReviewRepository, ReviewUploadRecord
from app.services.review.worker import ReviewWorkerQueue

if TYPE_CHECKING:
    # `app.agents.reviewer` ↔ `app.services.review` form an import cycle
    # (`reviewer` needs review's record types; review's service drives
    # `reviewer`). The runtime users below import `analyze_review`
    # function-locally; only the type name is needed at module scope,
    # and `from __future__ import annotations` keeps it a string.
    from app.agents.reviewer import ReviewerResult

logger = structlog.get_logger(__name__)

# `up_` prefix mirrors the example in `schemas/review.py` and keeps
# IDs visually distinguishable from `s_` (session) / `u_` (user) ids
# in trace logs and ad-hoc DB queries.
_UPLOAD_ID_PREFIX = "up_"
_UPLOAD_ID_HEX_LEN = 16

# Review is a background, single-shot analysis behind the upload spinner
# — not latency-critical — so a transient LLM transport blip (both router
# providers missing the first-byte budget once on a flaky / recovering
# network) should be retried rather than failing the whole upload. Bounded
# so a genuine outage still settles into `failed` in a few seconds. Linear
# backoff gives the network a moment between attempts.
#
# Module-level so tests can monkeypatch the backoff to 0 and run fast.
_LLM_RETRY_ATTEMPTS = 3
_LLM_RETRY_BACKOFF_S = 0.6


class _IdFactory(Protocol):
    def __call__(self) -> str: ...


class _Clock(Protocol):
    def __call__(self) -> datetime: ...


def _default_id_factory() -> str:
    return _UPLOAD_ID_PREFIX + secrets.token_hex(_UPLOAD_ID_HEX_LEN // 2)


def _default_clock() -> datetime:
    return datetime.now(UTC)


class ReviewInputBlockedError(RuntimeError):
    """Route maps to 400 USER_INPUT_BLOCKED — moderation rejected the
    review text before any persistence or LLM work. Mirrors the
    `UserInputBlockedError` contract from `TurnService` so frontend
    error handling is uniform across sandbox + review flows."""

    def __init__(self, *, categories: tuple[str, ...]) -> None:
        super().__init__(f"review input blocked by moderation: {categories}")
        self.categories = categories


class ReviewService:
    """Stateless helper — every request gets its own service call but
    the underlying `repo`, `provider`, `moderation`, and `queue`
    singletons are shared."""

    def __init__(
        self,
        *,
        repo: ReviewRepository,
        provider: LLMProvider,
        moderation: ModerationService,
        queue: ReviewWorkerQueue,
        langfuse_client: Langfuse | None = None,
        id_factory: _IdFactory | None = None,
        clock: _Clock | None = None,
    ) -> None:
        self._repo = repo
        self._provider = provider
        self._moderation = moderation
        self._queue = queue
        # `None` is a real, supported value — when LANGFUSE_* keys
        # aren't configured `get_langfuse_client` returns None and
        # `begin_review_trace` degrades to a no-op `TurnTrace`. Dev
        # runs without a Langfuse instance work unchanged.
        self._langfuse_client = langfuse_client
        self._id_factory: _IdFactory = id_factory or _default_id_factory
        self._clock: _Clock = clock or _default_clock

    async def create_upload(
        self,
        *,
        text: str,
        user_id: str,
        is_minor: bool,
        trace_id: str,
    ) -> ReviewUploadRecord:
        """Input moderation → persist `processing` → enqueue worker → return record.

        Returns the record in the `processing` state. The worker (run
        by `self._queue`) flips it to `done` / `failed` afterwards.
        Clients poll `GET /v1/review/uploads/{id}` to see the final
        state.

        Raises `ReviewInputBlockedError` (route → 400) when input
        moderation says block — no row is written and no LLM work is
        scheduled.
        """
        # ---- INPUT MODERATION (pre-persist, pre-LLM) ----
        # `block` short-circuits before any DB or queue work. `warn` /
        # `redirect` proceed; minors had `warn` already elevated to
        # `block` inside the service via `_apply_minor_strictness`.
        #
        # A-37: when the moderation backend itself is unavailable we
        # fail-open (matching the sandbox surface). The upload still
        # gets persisted and analyzed; the Langfuse trace just carries
        # `verdict_input:backend_failed` instead of a real verdict so
        # the outage is greppable. Output-side moderation (A-29) on
        # the analyzer's reply is unaffected.
        try:
            input_decision = await self._moderation.check(
                ModerationCheckRequest(content=text, context="user_input"),
                user_id=user_id,
                is_minor=is_minor,
                trace_id=trace_id,
            )
        except ModerationBackendError as exc:
            logger.warning(
                "review_input_moderation_unavailable",
                trace_id=trace_id,
                user_id=user_id,
                backend=exc.backend,
                error=str(exc),
            )
            input_verdict = "backend_failed"
        else:
            if input_decision.verdict == "block":
                logger.info(
                    "review_input_blocked",
                    trace_id=trace_id,
                    user_id=user_id,
                    categories=list(input_decision.categories),
                )
                raise ReviewInputBlockedError(categories=tuple(input_decision.categories))
            input_verdict = input_decision.verdict

        # ---- PERSIST processing record ----
        upload_id = self._id_factory()
        created_at = self._clock()
        await self._repo.create(
            ReviewUploadRecord(
                upload_id=upload_id,
                user_id=user_id,
                text=text,
                status="processing",
                turns=(),
                summary_score=None,
                summary_top_failures=(),
                summary_improvements=(),
                created_at=created_at,
                completed_at=None,
            )
        )

        # ---- ENQUEUE the post-persist work ----
        # `input_verdict` rides through to `_process_upload` so the
        # Langfuse trace can tag it (A-25). `block` already early-
        # returned above, so this is `allow` / `warn` / `redirect`
        # for a successful mod call, or `backend_failed` for an A-37
        # fail-open. The variable is set in the try/else block above.

        async def _do_analysis() -> None:
            await self._process_upload(
                upload_id=upload_id,
                text=text,
                user_id=user_id,
                is_minor=is_minor,
                trace_id=trace_id,
                input_verdict=input_verdict,
            )

        await self._queue.enqueue(_do_analysis)

        # Re-fetch so the route sees the canonical record. With the
        # in-process queue the task hasn't started yet, so this returns
        # the `processing` record we just wrote. (A `SyncWorkerQueue`
        # test impl runs the work inline, in which case this returns
        # the already-folded `done` / `failed` record — that's why the
        # route's contract is "whatever status the record now has".)
        final = await self._repo.get(upload_id)
        if final is None:
            raise RuntimeError(f"review repo lost upload_id {upload_id!r} immediately after write")
        return final

    async def get_upload(
        self,
        upload_id: str,
        *,
        user_id: str,
    ) -> ReviewUploadRecord | None:
        """Return the record only if it exists AND belongs to `user_id`.

        Mismatched-owner returns None (not a typed error) so the route
        can map missing-or-not-yours to a single 404 — clients can't
        enumerate upload_ids by status code.
        """
        record = await self._repo.get(upload_id)
        if record is None or record.user_id != user_id:
            return None
        return record

    async def _process_upload(
        self,
        *,
        upload_id: str,
        text: str,
        user_id: str,
        is_minor: bool,
        trace_id: str,
        input_verdict: str,
    ) -> None:
        """Worker body — runs out-of-band on the queue.

        Drives `analyze_review`, runs output moderation, folds the
        result back via `update_result` OR drops it via `mark_failed`.
        Any uncaught exception bubbles to `worker._run_with_logging`,
        which logs and lets the task end with the row still in
        `processing` — a polling client surfaces this as "still
        analyzing" until the user retries.

        Wrapped in a Langfuse `review_upload` trace so the LLM call
        shows up alongside `/turns` traces in the Langfuse UI. The
        trace's `record_generation` span captures the analyze_review
        round-trip; the trace's final `output` carries upload_id +
        status so analysts can filter `failed` runs without joining
        against the DB. When `LANGFUSE_*` keys aren't configured the
        trace is a no-op (`begin_review_trace` returns a no-op
        `TurnTrace`) so dev runs aren't slowed down.
        """
        trace = begin_review_trace(
            self._langfuse_client,
            input={"text_len": len(text)},
            # `upload_id` is promoted to Langfuse's top-level session
            # field (A-23). Review is single-shot today so each upload
            # is its own one-trace session; once a re-analysis path
            # lands those subsequent traces will land under the same
            # session row.
            metadata={
                "user_id": user_id,
                "trace_id": trace_id,
                "is_minor": is_minor,
            },
            session_id=upload_id,
            # A-40: trace_id + user_id let `record_generation` schedule
            # the per-call DB insert into `llm_calls`. `upload_id` is the
            # natural per-trace correlator (same value Langfuse already
            # sees as session_id); review's surface is hardcoded inside
            # `begin_review_trace`.
            trace_id=upload_id,
            user_id=user_id,
            # A-24: surface + minor tags for cross-cutting Langfuse
            # filtering. `minor:true` is the load-bearing one — it
            # lets analysts isolate moderation behaviors that only
            # fire for under-18 callers (PRD §3.0.5 C).
            # A-25: input verdict tag (`allow` / `warn` / `redirect`;
            # `block` early-returned before this trace exists). The
            # output verdict from the post-analysis re-check is added
            # later via `add_tags` once `_run_analysis_traced` knows it.
            tags=[
                "surface:review",
                f"minor:{'true' if is_minor else 'false'}",
                f"verdict_input:{input_verdict}",
            ],
        )

        try:
            await self._run_analysis_traced(
                upload_id=upload_id,
                text=text,
                user_id=user_id,
                is_minor=is_minor,
                trace_id=trace_id,
                trace=trace,
            )
        except Exception as exc:
            # `_run_with_logging` (in `worker.py`) is what eventually
            # catches + logs this; calling `trace.fail` here makes the
            # Langfuse UI show the trace as ERROR before the worker
            # wrapper logs the structured event. The exception then
            # re-raises so the wrapper's `logger.exception` path runs.
            trace.fail(exc)
            raise

    async def _run_analysis_traced(
        self,
        *,
        upload_id: str,
        text: str,
        user_id: str,
        is_minor: bool,
        trace_id: str,
        trace: TurnTrace,
    ) -> None:
        usage: list[TokenUsage] = []
        result: ReviewerResult | None = await self._analyze_with_retry(
            text=text,
            usage=usage,
            upload_id=upload_id,
            trace_id=trace_id,
        )

        # Record the analyze_review LLM call as one generation under
        # this trace. We record even on `None` so failed analyses show
        # up on the Langfuse UI with empty output — otherwise an
        # operator looking at a `failed` upload would see the trace
        # but no generation span, which is harder to debug. Usage is
        # populated by the provider when it surfaced token counts
        # (A-27); falsy means we tried but the upstream didn't
        # include them — the trace just lands without usage.
        trace.record_generation(
            name="analyze_review",
            model=getattr(self._provider, "name", "unknown"),
            input={"text_len": len(text)},
            output=_summarize_result_for_trace(result),
            usage=usage[0] if usage else None,
        )

        if result is not None:
            output_passed, output_verdict = await self._output_passes_moderation(
                result,
                user_id=user_id,
                is_minor=is_minor,
                trace_id=trace_id,
                upload_id=upload_id,
            )
            # A-25: tag the output-side verdict so analysts can
            # filter for traces where the LLM's coaching text itself
            # tripped a red-line (rare but operationally interesting).
            # `output_verdict is None` when there was no LLM text to
            # check (every turn neutral) OR when the moderation
            # backend itself failed — in both cases we treat-as-allow
            # so no tag is added.
            if output_verdict is not None:
                trace.add_tags([f"verdict_output:{output_verdict}"])
            if not output_passed:
                result = None

        completed_at = self._clock()
        final_status: str
        if result is None:
            await self._repo.mark_failed(upload_id, completed_at=completed_at)
            final_status = "failed"
        else:
            await self._repo.update_result(
                upload_id,
                turns=result.turns,
                summary_score=result.summary_score,
                summary_top_failures=result.summary_top_failures,
                summary_improvements=result.summary_improvements,
                completed_at=completed_at,
            )
            final_status = "done"

        trace.finish(
            output={
                "upload_id": upload_id,
                "status": final_status,
                "summary_score": result.summary_score if result is not None else None,
                "turn_count": len(result.turns) if result is not None else 0,
            }
        )

    async def _analyze_with_retry(
        self,
        *,
        text: str,
        usage: list[TokenUsage],
        upload_id: str,
        trace_id: str,
    ) -> ReviewerResult | None:
        """Run `analyze_review`, retrying on transient LLM transport
        failures (`LLMError`) with linear backoff.

        Returns the parsed result, or None when:
          * every attempt raised `LLMError` — a genuine outage (both
            router providers unreachable). The caller marks the upload
            `failed`.
          * the LLM responded but the input/output was unparseable
            (`analyze_review`'s own None). That is NOT retried — a
            re-sample rarely fixes a structural parse miss and we don't
            want to triple the token cost on every malformed reply.

        `usage` is cleared before each attempt and ends populated from
        the attempt that reached the provider, so the trace still records
        token counts on the call that actually produced output.
        """
        # Imported here, not at module scope, to break the
        # `reviewer` ↔ `review.service` import cycle (see TYPE_CHECKING
        # block above). By call time `app.agents.reviewer` is fully loaded.
        from app.agents.reviewer import analyze_review

        for attempt in range(1, _LLM_RETRY_ATTEMPTS + 1):
            usage.clear()
            try:
                return await analyze_review(self._provider, text=text, usage_sink=usage)
            except LLMError as exc:
                is_last = attempt == _LLM_RETRY_ATTEMPTS
                logger.warning(
                    "review_llm_attempt_failed",
                    upload_id=upload_id,
                    trace_id=trace_id,
                    provider=getattr(self._provider, "name", "unknown"),
                    attempt=attempt,
                    max_attempts=_LLM_RETRY_ATTEMPTS,
                    will_retry=not is_last,
                    error=str(exc),
                )
                if is_last:
                    return None
                # Linear backoff (0.6s, 1.2s, …) — a brief pause lets a
                # flaky / recovering network reconnect before the next try.
                await asyncio.sleep(_LLM_RETRY_BACKOFF_S * attempt)
        return None  # unreachable: the loop always returns on its last attempt

    async def _output_passes_moderation(
        self,
        result: ReviewerResult,
        *,
        user_id: str,
        is_minor: bool,
        trace_id: str,
        upload_id: str,
    ) -> tuple[bool, str | None]:
        """Re-check LLM-generated coaching text against the red-line list.

        Returns `(passes, verdict)`:
          * `passes`: True when the output is safe to surface;
                     False on `block` / `redirect`.
          * `verdict`: the literal moderation verdict for trace
                     tagging. One of:
                       * `"allow"`/`"warn"`/`"redirect"`/`"block"`
                         (A-25) — backend produced a real decision
                       * `"backend_failed"` (A-34) — backend itself
                         raised; treat-as-allow on the result side
                         but tag the trace so analysts can spot
                         outages without parsing logs
                       * `None` — empty coaching text; no backend
                         call was made, no tag added

        The user-uploaded text already passed input moderation, but the
        LLM's `reason` / `better` suggestions could echo or rephrase
        unsafe content. We concatenate everything the LLM produced and
        run one moderation pass; on `block` (or `redirect`, which
        carries a crisis-line resource and means "this conversation is
        not the right thing for us to coach on") we drop the result.

        Backend errors here are caught and treated as `allow` —
        otherwise an Aliyun outage would invalidate analyses the user
        already paid for. Input-side backend errors stay uncaught (no
        orphan row was created yet, so the 5xx is clean).
        """
        text = _flatten_for_moderation(result)
        if not text:
            # No coaching text was generated (all turns neutral / win
            # with no extra commentary). Nothing to re-check.
            return True, None

        try:
            decision = await self._moderation.check(
                ModerationCheckRequest(content=text, context="ai_output"),
                user_id=user_id,
                is_minor=is_minor,
                trace_id=trace_id,
            )
        except ModerationBackendError as exc:
            logger.warning(
                "review_output_moderation_unavailable",
                upload_id=upload_id,
                trace_id=trace_id,
                error=str(exc),
            )
            # A-34: see turn_service for the rationale — return the
            # `"backend_failed"` sentinel so the caller's existing
            # `verdict_output:{verdict}` tag adder surfaces outages
            # under the same key as real verdicts.
            return True, "backend_failed"

        if decision.verdict in ("block", "redirect"):
            logger.warning(
                "review_output_blocked",
                upload_id=upload_id,
                trace_id=trace_id,
                verdict=decision.verdict,
                categories=list(decision.categories),
            )
            return False, decision.verdict
        return True, decision.verdict


def _summarize_result_for_trace(result: ReviewerResult | None) -> dict[str, object]:
    """Compress a `ReviewerResult` into a small dict for the Langfuse
    `generation.output` field. We never log the raw `text` (PII risk)
    or the full `better` suggestions (verbose, and the persisted row
    has them anyway). The summary makes the Langfuse UI useful for
    "did the LLM produce anything reasonable" inspection without
    being a privacy hazard."""
    if result is None:
        return {"parsed": False}
    return {
        "parsed": True,
        "turn_count": len(result.turns),
        "summary_score": result.summary_score,
        "top_failure_count": len(result.summary_top_failures),
        "improvement_count": len(result.summary_improvements),
    }


def _flatten_for_moderation(result: ReviewerResult) -> str:
    """Concatenate every LLM-generated string for one moderation pass.

    Joining with `\\n` keeps the cloud backend's per-line scoring useful
    while staying well under the 8000-char request cap (the per-turn
    fields are bounded at 500 chars and capped at 50 turns + 6 summary
    items, so even a worst-case payload is ≈ 51000 chars... in practice
    the prompt cap and item cap keep it much smaller, but we still
    truncate as a backstop).
    """
    parts: list[str] = []
    for turn in result.turns:
        if turn.reason:
            parts.append(turn.reason)
        if turn.better:
            parts.append(turn.better)
    parts.extend(result.summary_top_failures)
    parts.extend(result.summary_improvements)
    joined = "\n".join(p for p in parts if p)
    return joined[:8000]
