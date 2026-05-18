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

from app.schemas.moderation import (
    ModerationCategory,
    ModerationContext,
    ModerationVerdict,
)

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


# --- A-43 moderation event tail ---


# Hard cap on a single page. 500 is generous for an ops tail (which
# typically renders the last 50) without letting a runaway query pull
# 100k rows in one go. Pinned in the route + tests.
MAX_MODERATION_EVENTS_LIMIT = 500


class ModerationEventEntry(BaseModel):
    """One audit row as it appears in the ops tail.

    `content_hash` is the SHA-256 hex of the original request body
    (always 64 chars). Raw content is never returned — see
    `event_sink.py` for why: the audit log itself would otherwise
    become a red-line corpus the moment we hand it back over HTTP.

    `context`, `verdict`, `categories` reuse the user-facing Literals
    from `app.schemas.moderation` so a schema drift on either side
    surfaces in one place — the moderation pipeline and the audit
    surface stay aligned by construction.
    """

    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1, description="UUID hex of the audit row.")
    user_id: str = Field(min_length=1)
    session_id: str | None = Field(
        default=None, description="Set when the moderation call came from inside a Session/Turn."
    )
    content_hash: str = Field(
        min_length=64,
        max_length=64,
        description="SHA-256 hex of the raw content. Raw text is never returned.",
    )
    content_length: int = Field(
        ge=0,
        description="Length of the original UTF-8 string — lets ops see size without the body.",
    )
    context: ModerationContext
    verdict: ModerationVerdict
    categories: list[ModerationCategory]
    score: float = Field(ge=0.0, le=1.0)
    backend: str = Field(
        min_length=1,
        description="Which backend produced the decision (e.g. `aliyun`, `local_dict`).",
    )
    trace_id: str = Field(min_length=1)
    created_at: datetime


class ModerationEventsResponse(BaseModel):
    """Page of recent moderation decisions, newest first.

    `since` / `until` / `user_id` / `verdict` echo the resolved
    filters so the renderer doesn't have to re-derive them from the
    query string (and so a downstream cache key can be built off the
    response alone). `limit` is the cap the route applied; `count`
    is how many rows actually came back (≤ limit).
    """

    model_config = ConfigDict(extra="forbid")

    since: datetime | None
    until: datetime | None
    user_id: str | None = Field(
        default=None,
        description="Echoes the user_id filter when one was applied. Null = unfiltered.",
    )
    verdict: ModerationVerdict | None = Field(
        default=None,
        description="Echoes the verdict filter when one was applied. Null = unfiltered.",
    )
    limit: int = Field(ge=1, le=MAX_MODERATION_EVENTS_LIMIT)
    count: int = Field(ge=0)
    events: list[ModerationEventEntry]
    generated_at: datetime


__all__ = [
    "MAX_MODERATION_EVENTS_LIMIT",
    "ModerationEventEntry",
    "ModerationEventsResponse",
    "TokenCostBreakdownEntry",
    "TokenCostResponse",
    "TokenCostTotals",
    "TokenCostWindow",
]
