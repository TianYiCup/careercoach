"""ASR DI factory.

A-17 only wires the `dummy` backend; a future PR will add `aliyun`
and/or `tencent` and flip the production default. Callers never see
the concrete adapter — they depend on the `ASRProvider` Protocol and
the factory hands them whichever one settings selected.

The `@lru_cache` makes the provider process-wide. Tests override via
`app.dependency_overrides[get_asr_provider] = ...` or by calling
`get_asr_provider.cache_clear()` before mutating settings (see
`tests/test_asr_factory.py`).
"""

from __future__ import annotations

from functools import lru_cache

import structlog

from app.asr.dummy import DummyASRProvider
from app.asr.provider import ASRProvider
from app.config import get_settings

logger = structlog.get_logger(__name__)


@lru_cache(maxsize=1)
def get_asr_provider() -> ASRProvider:
    """Process-wide ASR provider singleton.

    Backend chosen via `settings.asr_backend`. A-17 ships only the
    `dummy` impl so the only supported value is `"dummy"`. The
    `Literal` type on the settings field means an unsupported value
    fails at config-load time, not here.
    """
    settings = get_settings()
    backend = settings.asr_backend
    if backend == "dummy":
        logger.info("asr_provider_wired", backend="dummy")
        return DummyASRProvider()
    # Defensive: the Literal on the settings field forbids any other
    # value, so this branch is unreachable today. We keep the raise
    # so a future PR that widens the Literal but forgets to wire a
    # real adapter here fails loudly.
    raise ValueError(f"unsupported asr backend: {backend!r}")
