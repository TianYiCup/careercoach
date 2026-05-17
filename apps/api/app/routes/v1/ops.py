"""Ops-only `/v1/ops/*` HTTP surface — staff-side cost + diagnostics.

A-42 lands the first endpoint in this namespace:

  GET /v1/ops/token-cost?user_id=<id>&window=<1d|7d|30d|all>

Every endpoint here is gated by `require_ops_token` (A-41), which
checks the `X-Ops-Token` header. The dep fails closed when
`OPS_API_TOKEN` is unset — a forgotten env var must never silently
open the rollup endpoint to the public.

Reads via `LLMCallRepository.aggregate_by_user` (A-39 / A-40). No
endpoint here writes; ops routes that mutate state can land later
in their own files under the same prefix.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends, Query

from app.schemas.ops import (
    TokenCostBreakdownEntry,
    TokenCostResponse,
    TokenCostTotals,
    TokenCostWindow,
)
from app.services.llm_calls import (
    LLMCallAggregate,
    LLMCallBreakdownEntry,
    LLMCallRepository,
    get_llm_call_repository,
)
from app.services.ops import require_ops_token

router = APIRouter(prefix="/ops", tags=["ops"])


def _get_repo() -> LLMCallRepository:
    """Tiny wrapper so FastAPI's `Depends` sees a callable rather
    than a bare `lru_cache`-d function — keeps `dependency_overrides`
    in tests symmetrical with the user-side route deps."""
    return get_llm_call_repository()


_WINDOW_DURATIONS: dict[TokenCostWindow, timedelta | None] = {
    "1d": timedelta(days=1),
    "7d": timedelta(days=7),
    "30d": timedelta(days=30),
    # `all` passes no bounds to the repo — useful for "what has this
    # user spent across the full retention window we have on file".
    "all": None,
}


@router.get(
    "/token-cost",
    response_model=TokenCostResponse,
    summary="Per-user LLM token spend rollup over a fixed time window",
    description=(
        "Aggregates `llm_calls` rows for one user across the requested "
        "window. Returns headline totals plus by-model and by-surface "
        "breakdowns, each sorted by `total_tokens` descending. "
        "`window=all` skips time bounds; otherwise the response's "
        "`since`/`until` echo the resolved bounds for cacheability."
    ),
    responses={
        401: {"description": "missing or wrong X-Ops-Token header"},
        503: {"description": "ops endpoints disabled (OPS_API_TOKEN unset)"},
    },
    dependencies=[Depends(require_ops_token)],
)
async def get_token_cost(
    user_id: str = Query(
        ...,
        min_length=1,
        description=(
            "The user whose spend to roll up. Pass the literal "
            "`system` for non-user-attributed calls (cron, background)."
        ),
        examples=["u_018f3a8b1c2d7e3a"],
    ),
    window: TokenCostWindow = Query(
        default="7d",
        description=(
            "Time window for the rollup. `1d` / `7d` / `30d` are "
            "rolling windows ending at request time; `all` is "
            "unbounded."
        ),
    ),
    repo: LLMCallRepository = Depends(_get_repo),
) -> TokenCostResponse:
    """Resolve the window to since/until, call the repo, repackage
    the dataclass result as the wire schema, stamp `generated_at`."""
    now = datetime.now(UTC)
    duration = _WINDOW_DURATIONS[window]
    since = now - duration if duration is not None else None
    until = now if duration is not None else None

    aggregate = await repo.aggregate_by_user(user_id, since=since, until=until)

    return _build_response(aggregate=aggregate, window=window, generated_at=now)


def _build_response(
    *,
    aggregate: LLMCallAggregate,
    window: TokenCostWindow,
    generated_at: datetime,
) -> TokenCostResponse:
    """Repackage the repo's dataclass result as the wire schema.

    Pulled into a helper so the route stays a thin pass-through and
    a future caller (e.g. a CLI that bypasses the route) can reuse
    the same transformation without re-implementing it.
    """
    return TokenCostResponse(
        user_id=aggregate.user_id,
        window=window,
        since=aggregate.since,
        until=aggregate.until,
        totals=TokenCostTotals(
            call_count=aggregate.totals.call_count,
            prompt_tokens=aggregate.totals.prompt_tokens,
            completion_tokens=aggregate.totals.completion_tokens,
            total_tokens=aggregate.totals.total_tokens,
        ),
        by_model=[_breakdown_entry(e) for e in aggregate.by_model],
        by_surface=[_breakdown_entry(e) for e in aggregate.by_surface],
        generated_at=generated_at,
    )


def _breakdown_entry(entry: LLMCallBreakdownEntry) -> TokenCostBreakdownEntry:
    return TokenCostBreakdownEntry(
        key=entry.key,
        call_count=entry.call_count,
        prompt_tokens=entry.prompt_tokens,
        completion_tokens=entry.completion_tokens,
        total_tokens=entry.total_tokens,
    )


__all__ = ["router"]
