"""LLMCall audit row — per-generation token + model accounting.

One row per call to a Langfuse `generation` (sandbox roleplay, coach
hint, judge, reviewer, copilot hint, etc.). The same data is already
shipped to Langfuse (A-27 surfaces it via `record_generation(usage=...)`);
this table is the system-of-record we control so per-user / per-window
cost rollups don't have to round-trip through Langfuse's public API.

A-39 lays the persistence bones only. Writer wiring (the actual
`record_generation` → `insert(record)` hook in callers) is A-40, and
the `/v1/ops/token-cost` read endpoint + ops auth gate are A-41/A-42.

Why a separate table rather than extending `traces`/`generations`
----------------------------------------------------------------
We don't model Langfuse traces locally — `TurnTrace` is a thin wrapper
that emits to Langfuse and returns. Persisting only the cost-relevant
slice (tokens + model + user + surface) keeps the schema narrow and
avoids drifting into a parallel observability store.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, Integer, String, func
from sqlalchemy.dialects.postgresql import UUID as PgUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class LLMCall(Base):
    """Audit record for one LLM generation's token accounting."""

    __tablename__ = "llm_calls"

    id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )

    # Links back to the Langfuse trace so an analyst staring at a cost
    # row can paste the trace_id into the Langfuse UI to see the full
    # context (prompt, output, sibling generations). Required: every
    # call site already has a trace_id (TurnTrace wraps the trace
    # handle that owns it).
    trace_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)

    # Who paid for this token spend. Required — system-internal calls
    # without a user (e.g. cron jobs) should pass a stable sentinel like
    # "system" rather than NULL so per-user aggregates don't accidentally
    # lump them with anonymous-user buckets. Indexed because the rollup
    # endpoint's hot query is `WHERE user_id = $1 AND created_at >= $2`.
    user_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)

    # Which subsystem made the call: sandbox | review | copilot | agent.
    # Plain String(16) rather than an enum so adding a surface (e.g. a
    # future `share` flow) is a config-level change, not a DDL change.
    # Indexed so the rollup can answer "how much of this user's spend
    # came from sandbox vs copilot?" without a full scan.
    surface: Mapped[str] = mapped_column(String(16), nullable=False, index=True)

    # The model id reported by the LLM provider (e.g. "deepseek-chat",
    # "qwen-max"). Captured verbatim — we don't normalize vendor naming
    # because the cost table is keyed on the exact billing string the
    # vendor uses. 64 chars covers the longest known model ids with
    # headroom for date-suffixed snapshots.
    model: Mapped[str] = mapped_column(String(64), nullable=False)

    # Token counts as reported by the provider. We store all three
    # rather than computing total = prompt + completion because some
    # vendors charge for reasoning / cached prompt tokens that are
    # included in `total_tokens` but split out separately on the bill.
    # Matching the provider's own arithmetic keeps our rollup honest.
    prompt_tokens: Mapped[int] = mapped_column(Integer, nullable=False)
    completion_tokens: Mapped[int] = mapped_column(Integer, nullable=False)
    total_tokens: Mapped[int] = mapped_column(Integer, nullable=False)

    # Indexed because every rollup query bounds on a time window
    # (`WHERE created_at >= now() - interval '7 days'`).
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        index=True,
    )

    def __repr__(self) -> str:
        return (
            f"LLMCall(id={self.id!s}, user_id={self.user_id}, "
            f"model={self.model}, total_tokens={self.total_tokens})"
        )
