"""LLM router DI factory.

Wires the DeepSeek primary + Qwen backup adapters per the spike report
(`docs/sprint-0/llm-spike-report.md`). The `@lru_cache` makes the
router process-wide; tests override via `app.dependency_overrides`.

API keys come from `Settings`. Adapters refuse to construct with an
empty key (early-fail by design), so this factory skips any provider
whose key is empty and assembles whatever's left. If none are
configured we hand back a router wrapping a single `NoCredentialsProvider`
that errors out cleanly on first `stream_chat` — that way the app
still boots in dev without keys and only `/turns` blows up.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from functools import lru_cache

import structlog

from app.config import get_settings
from app.llm.deepseek import DeepSeekProvider
from app.llm.errors import LLMAuthError
from app.llm.provider import (
    DEFAULT_TEMPERATURE,
    DEFAULT_TIMEOUT_SECONDS,
    LLMProvider,
)
from app.llm.qwen import QwenProvider
from app.llm.router import LLMRouter
from app.llm.types import Message

logger = structlog.get_logger(__name__)


class NoCredentialsProvider:
    """Placeholder used when every adapter is missing its API key.

    Raises on the first `stream_chat` call so dev runs without keys
    still boot — only routes that actually talk to an LLM fail, with
    the same `LLMError` shape any other auth failure would produce.
    """

    name = "no_credentials"

    async def stream_chat(
        self,
        messages: list[Message],
        *,
        temperature: float = DEFAULT_TEMPERATURE,
        timeout: float = DEFAULT_TIMEOUT_SECONDS,
    ) -> AsyncIterator[str]:
        _ = (messages, temperature, timeout)
        raise LLMAuthError(
            "no LLM credentials configured — set DEEPSEEK_API_KEY or QWEN_API_KEY",
            provider=self.name,
        )
        # Unreachable, but keeps the function an async generator for typing.
        yield ""  # pragma: no cover


@lru_cache(maxsize=1)
def get_llm_router() -> LLMRouter:
    """Default chat router used by route handlers."""
    settings = get_settings()
    providers: list[LLMProvider] = []

    deepseek_key = settings.deepseek_api_key.get_secret_value()
    if deepseek_key:
        providers.append(
            DeepSeekProvider(
                api_key=deepseek_key,
                base_url=settings.deepseek_base_url,
                model=settings.deepseek_model,
            )
        )

    qwen_key = settings.qwen_api_key.get_secret_value()
    if qwen_key:
        providers.append(
            QwenProvider(
                api_key=qwen_key,
                base_url=settings.qwen_base_url,
                model=settings.qwen_model,
            )
        )

    if not providers:
        logger.warning(
            "llm_router_no_credentials",
            note="boot OK but /v1/sessions/{id}/turns will 5xx until keys are set",
        )
        providers.append(NoCredentialsProvider())

    primary, *backups = providers
    router = LLMRouter(primary=primary, backups=tuple(backups))
    logger.info(
        "llm_router_wired",
        primary=primary.name,
        backups=[p.name for p in backups],
    )
    return router
