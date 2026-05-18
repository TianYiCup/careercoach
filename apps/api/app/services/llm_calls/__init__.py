"""LLM call persistence service package — A-39 foundation.

Public surface:
  * `LLMCallRecord` immutable record (insert input shape)
  * `LLMCallAggregate` / `LLMCallTotals` / `LLMCallBreakdownEntry`
    aggregate result shapes
  * `LLMCallRepository` Protocol + `InMemoryLLMCallRepository` +
    `PostgresLLMCallRepository`
  * `get_llm_call_repository()` factory singleton

A-40 adds the observability hook (calls `insert(...)` from
`record_generation`). A-41 adds ops auth. A-42 ships the
`/v1/ops/token-cost` endpoint that calls `aggregate_by_user(...)`.
"""

from functools import lru_cache

import structlog

from app.config import get_settings
from app.db.session import async_session_factory
from app.services.llm_calls.repository import (
    InMemoryLLMCallRepository,
    LLMCallAggregate,
    LLMCallBreakdownEntry,
    LLMCallDailyAggregate,
    LLMCallDailyEntry,
    LLMCallRecord,
    LLMCallRepository,
    LLMCallTotals,
    PostgresLLMCallRepository,
)

logger = structlog.get_logger(__name__)


@lru_cache(maxsize=1)
def get_llm_call_repository() -> LLMCallRepository:
    """Process-wide LLM-call repo singleton. Backend chosen via settings —
    `memory` for dev / tests, `postgres` for production. Mirrors the
    `copilot_repo_backend` factory pattern."""
    backend = get_settings().llm_calls_repo_backend
    if backend == "postgres":
        logger.info("llm_call_repository_wired", backend="postgres")
        return PostgresLLMCallRepository(async_session_factory)
    logger.info("llm_call_repository_wired", backend="memory")
    return InMemoryLLMCallRepository()


__all__ = [
    "InMemoryLLMCallRepository",
    "LLMCallAggregate",
    "LLMCallBreakdownEntry",
    "LLMCallDailyAggregate",
    "LLMCallDailyEntry",
    "LLMCallRecord",
    "LLMCallRepository",
    "LLMCallTotals",
    "PostgresLLMCallRepository",
    "get_llm_call_repository",
]
