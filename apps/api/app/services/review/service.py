"""Review-mode (复盘师) HTTP service — orchestrates analyze_review + persistence.

Why this layer exists between the route and the repo
-----------------------------------------------------
A-9 landed the persistence shell, A-10 the LLM analyzer. The route
needs to mint an upload id, write a `processing` row, run the
analyzer, fold the result back into the row (or `mark_failed` on
errors), and re-fetch the canonical record for the response. That is
*orchestration*, not HTTP plumbing — keeping it out of the route
matches the `SessionService` / `TurnService` precedent so future
work (background queue handoff, retry policy, langfuse trace) has a
clear seam.

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

from app.agents.reviewer import analyze_review
from app.llm import LLMError, LLMProvider
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


class ReviewService:
    """Stateless helper — every request gets its own service call but
    the underlying `repo` and `provider` singletons are shared."""

    def __init__(
        self,
        *,
        repo: ReviewRepository,
        provider: LLMProvider,
        id_factory: _IdFactory | None = None,
        clock: _Clock | None = None,
    ) -> None:
        self._repo = repo
        self._provider = provider
        self._id_factory: _IdFactory = id_factory or _default_id_factory
        self._clock: _Clock = clock or _default_clock

    async def create_upload(
        self,
        *,
        text: str,
        user_id: str,
    ) -> ReviewUploadRecord:
        """Mint id → persist `processing` → analyze → fold result → return canonical record.

        Wraps `analyze_review` in a `LLMError` catch so transient
        upstream failures land as `status="failed"` rather than 500ing
        the request. Anything past `LLMError` is unexpected and
        deliberately bubbles (DB outages etc).
        """
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

        try:
            result = await analyze_review(self._provider, text=text)
        except LLMError as exc:
            logger.warning(
                "review_llm_failed",
                upload_id=upload_id,
                provider=getattr(self._provider, "name", "unknown"),
                error=str(exc),
            )
            result = None

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
