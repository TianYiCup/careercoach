"""Review-mode (复盘师) HTTP service — orchestrates analyze_review + persistence.

Why this layer exists between the route and the repo
-----------------------------------------------------
A-9 landed the persistence shell, A-10 the LLM analyzer, A-11 the route
shell. A-12 (this revision) adds the moderation cascade so PRD §3.0.5
red-line content can't reach the LLM and any echo of it in the LLM
output can't reach storage. The route stays HTTP plumbing; orchestration
(id mint → input mod → persist → analyze → output mod → fold result →
re-fetch) is one method here so future work (queue handoff, Langfuse
trace, retry policy) only touches one file.

Sync analysis for v0
--------------------
v0 calls `analyze_review` synchronously inside `create_upload`. PRD
§3.3 US-C1 L4 caps text at 5000 chars and budgets ≤ 2s end-to-end,
which today's LLM round-trip just about fits. When the budget breaks
(or we want retry / fan-out), the queue handoff lands in A-13+ and
this method becomes "enqueue + return processing", with the worker
calling `update_result` / `mark_failed` from the side. The repo
contract is already idempotent (DELETE+INSERT on update), so the
async migration is a strict superset of today's flow.

Moderation policy for review
----------------------------
INPUT moderation runs **before any persistence or LLM call** so a
blocked upload leaves no orphan rows and burns no LLM budget. Verdict
mapping mirrors `TurnService.validate_turn_request`:
  * `block`    → raise `ReviewInputBlockedError` → route 400
  * `warn`     → proceed (already promoted to `block` for minors via
                 `_apply_minor_strictness`)
  * `redirect` → proceed; the dedicated `/v1/moderation/check` endpoint
                 is where the frontend renders the crisis-line resource
  * `allow`    → proceed

OUTPUT moderation runs **after** the LLM call. The LLM could echo a
red-line phrase from the user's text or generate one in `better`
suggestions, so we re-check the concatenated reasons + better hints +
summary lists. Verdict mapping is stricter — anything but `allow` /
`warn` flips the upload to `failed` because we'd be storing harmful
content otherwise. Backend errors during output moderation are caught
and treated as "no signal, allow" so an Aliyun outage doesn't trash
analyses the user already paid for; input-side backend errors stay
uncaught (matches `TurnService` behavior — 5xx instead of silent pass).

Ownership semantics for `get_upload`
------------------------------------
Returns None for both "no such row" and "row exists but belongs to a
different user" — the route layer maps both to 404 so a probing
client can't enumerate upload_ids by status code.
"""

from __future__ import annotations

import secrets
from datetime import UTC, datetime
from typing import Protocol

import structlog

from app.agents.reviewer import ReviewerResult, analyze_review
from app.llm import LLMError, LLMProvider
from app.schemas.moderation import ModerationCheckRequest
from app.services.moderation.backend import ModerationBackendError
from app.services.moderation.service import ModerationService
from app.services.review.repository import ReviewRepository, ReviewUploadRecord

logger = structlog.get_logger(__name__)

# `up_` prefix mirrors the example in `schemas/review.py` and keeps
# IDs visually distinguishable from `s_` (session) / `u_` (user) ids
# in trace logs and ad-hoc DB queries.
_UPLOAD_ID_PREFIX = "up_"
_UPLOAD_ID_HEX_LEN = 16


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
    the underlying `repo`, `provider`, and `moderation` singletons are
    shared."""

    def __init__(
        self,
        *,
        repo: ReviewRepository,
        provider: LLMProvider,
        moderation: ModerationService,
        id_factory: _IdFactory | None = None,
        clock: _Clock | None = None,
    ) -> None:
        self._repo = repo
        self._provider = provider
        self._moderation = moderation
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
        """Input moderation → mint id → persist `processing` → analyze →
        output moderation → fold result → return canonical record.

        Raises `ReviewInputBlockedError` (route → 400) when input
        moderation says block. LLM errors and output-moderation blocks
        both flip the persisted upload to `status="failed"` and the
        method still returns the record (route → 200).
        """
        # ---- INPUT MODERATION (pre-persist, pre-LLM) ----
        # `block` short-circuits before any DB or LLM work. `warn` /
        # `redirect` proceed; minors had `warn` already elevated to
        # `block` inside the service via `_apply_minor_strictness`.
        input_decision = await self._moderation.check(
            ModerationCheckRequest(content=text, context="user_input"),
            user_id=user_id,
            is_minor=is_minor,
            trace_id=trace_id,
        )
        if input_decision.verdict == "block":
            logger.info(
                "review_input_blocked",
                trace_id=trace_id,
                user_id=user_id,
                categories=list(input_decision.categories),
            )
            raise ReviewInputBlockedError(categories=tuple(input_decision.categories))

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

        # ---- ANALYZE ----
        try:
            result = await analyze_review(self._provider, text=text)
        except LLMError as exc:
            logger.warning(
                "review_llm_failed",
                upload_id=upload_id,
                trace_id=trace_id,
                provider=getattr(self._provider, "name", "unknown"),
                error=str(exc),
            )
            result = None

        # ---- OUTPUT MODERATION (defence in depth) ----
        if result is not None and not await self._output_passes_moderation(
            result,
            user_id=user_id,
            is_minor=is_minor,
            trace_id=trace_id,
            upload_id=upload_id,
        ):
            result = None

        # ---- FOLD result OR mark_failed ----
        completed_at = self._clock()
        if result is None:
            await self._repo.mark_failed(upload_id, completed_at=completed_at)
        else:
            await self._repo.update_result(
                upload_id,
                turns=result.turns,
                summary_score=result.summary_score,
                summary_top_failures=result.summary_top_failures,
                summary_improvements=result.summary_improvements,
                completed_at=completed_at,
            )

        final = await self._repo.get(upload_id)
        # We just wrote it, and `get` returns the same record we mutated.
        # Defensive: if the repo somehow lost it, surface as a clean 5xx
        # rather than handing the route a None and letting it silently 404.
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

    async def _output_passes_moderation(
        self,
        result: ReviewerResult,
        *,
        user_id: str,
        is_minor: bool,
        trace_id: str,
        upload_id: str,
    ) -> bool:
        """Re-check LLM-generated coaching text against the red-line list.

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
            return True

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
            return True

        if decision.verdict in ("block", "redirect"):
            logger.warning(
                "review_output_blocked",
                upload_id=upload_id,
                trace_id=trace_id,
                verdict=decision.verdict,
                categories=list(decision.categories),
            )
            return False
        return True


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
