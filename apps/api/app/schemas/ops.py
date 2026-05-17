"""Pydantic response schemas for `/v1/ops/*` endpoints.

Kept separate from the repo-side dataclasses (`LLMCallAggregate` &
friends in `app.services.llm_calls.repository`) because the wire
shape and the storage shape have different stability contracts:

* Repo dataclasses can evolve when an internal aggregation
  rewrite happens; they're not visible outside the process.
* These response models are the documented OpenAPI surface ops
  tooling reads. Renaming a field here is a breaking change.

The fields mirror the repo dataclasses 1:1 today, but keeping the
two layers separate means a future refactor that, say, normalizes
the breakdown shape (`name` vs `key`) won't accidentally rename
the JSON field at the same time.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

# Time window enum surfaced as a query string. `all` is unbounded
# (passes no since/until to the repo), useful for "what has this
# user spent across the full retention window we have on file".
TokenCostWindow = Literal["1d", "7d", "30d", "all"]


class TokenCostTotals(BaseModel):
    """Sum across the window — the headline cost numbers."""

    model_config = ConfigDict(extra="forbid")

    call_count: int = Field(ge=0)
    prompt_tokens: int = Field(ge=0)
    completion_tokens: int = Field(ge=0)
    total_tokens: int = Field(ge=0)


class TokenCostBreakdownEntry(BaseModel):
    """One row in a by-model or by-surface breakdown.

    `key` is the grouping value:
      * for `by_model` — the vendor model id verbatim (e.g.
        `"deepseek-chat"`, `"qwen-max"`)
      * for `by_surface` — `sandbox` / `review` / `copilot` / `agent`

    Sorted by `total_tokens` desc (the rollup endpoint surfaces
    most-expensive first so ops tooling renders without re-sorting).
    """

    model_config = ConfigDict(extra="forbid")

    key: str = Field(min_length=1)
    call_count: int = Field(ge=0)
    prompt_tokens: int = Field(ge=0)
    completion_tokens: int = Field(ge=0)
    total_tokens: int = Field(ge=0)


class TokenCostResponse(BaseModel):
    """Per-user × per-window LLM token spend.

    `since` / `until` echo the time window the route resolved from
    the `window` query param so a downstream renderer doesn't have
    to re-derive them. Both null when `window=all` (unbounded).

    `generated_at` is the server's UTC timestamp at the moment the
    rollup ran — lets ops cache responses with a known freshness
    floor without parsing dashboard timing themselves.
    """

    model_config = ConfigDict(extra="forbid")

    user_id: str = Field(min_length=1)
    window: TokenCostWindow
    since: datetime | None
    until: datetime | None
    totals: TokenCostTotals
    by_model: list[TokenCostBreakdownEntry]
    by_surface: list[TokenCostBreakdownEntry]
    generated_at: datetime


__all__ = [
    "TokenCostBreakdownEntry",
    "TokenCostResponse",
    "TokenCostTotals",
    "TokenCostWindow",
]
