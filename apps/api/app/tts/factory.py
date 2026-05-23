"""TTS DI factory + router wiring (A1.1 — dummy only).

Two surfaces:

* `get_tts_provider()` — return the configured primary provider in
  isolation, used by tests + the route that doesn't need failover.
* `get_tts_router()`   — return the failover-aware `TTSRouter` wrapping
  the configured chain. This is what the route handler depends on.

Backends in this PR
-------------------
  * `dummy`  — in-process synthetic WAV. Default; covers tests + dev
               runs without a TTS vendor.

The stacked follow-up PR (A1.2) widens the backend Literal and wires
`edge` (Microsoft Edge TTS over WSS, primary) + `aliyun` (NLS streaming
TTS, backup). Keeping that change scoped to A1.2 means A1.1 ships a
fully testable abstraction without dragging in 500 lines of vendor
adapter code.

The `@lru_cache` makes the provider process-wide. Tests override via
`app.dependency_overrides[get_tts_*] = …` or by calling
`get_tts_provider.cache_clear()` before mutating settings.
"""

from __future__ import annotations

from functools import lru_cache

import structlog

from app.config import get_settings
from app.tts.dummy import DummyTTSProvider
from app.tts.provider import TTSProvider
from app.tts.router import TTSRouter

logger = structlog.get_logger(__name__)


@lru_cache(maxsize=1)
def get_tts_provider() -> TTSProvider:
    """Process-wide primary TTS provider singleton (no failover).

    Used by tests and any caller that wants the single configured
    backend without the router's first-byte semantics.
    """
    settings = get_settings()
    backend = settings.tts_backend
    if backend == "dummy":
        logger.info("tts_provider_wired", backend="dummy")
        return DummyTTSProvider()
    # The Literal type on settings.tts_backend keeps this branch
    # unreachable from production. A1.2 widens both the literal and
    # this dispatch when Edge / Aliyun adapters land.
    raise ValueError(f"unsupported tts backend: {backend!r}")


@lru_cache(maxsize=1)
def get_tts_router() -> TTSRouter:
    """Process-wide failover-aware TTS router singleton.

    In A1.1 the chain is single-element (dummy only) — failover is
    impossible by definition. The router still wraps the provider so
    the route handler depends on a stable signature; A1.2 expands the
    chain to include Edge primary + Aliyun backup without touching the
    handler.
    """
    settings = get_settings()
    backend = settings.tts_backend
    if backend == "dummy":
        logger.info("tts_router_wired", backend="dummy", backups=())
        return TTSRouter(primary=DummyTTSProvider())
    raise ValueError(f"unsupported tts backend: {backend!r}")
