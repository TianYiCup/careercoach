"""Tests for the TTS DI factory + router wiring (A1.1).

Two surfaces:
  * `get_tts_provider()` — process-wide primary singleton
  * `get_tts_router()`   — process-wide failover-aware router

Both `lru_cache`-d like the ASR factory; tests must clear the cache to
re-read mutated settings.

The stacked follow-up PR (A1.2) adds backend-selection tests for
`edge` + `aliyun` alongside the adapter additions.
"""

from __future__ import annotations

from collections.abc import Generator

import pytest
from app.tts import (
    DummyTTSProvider,
    TTSProvider,
    TTSRouter,
    get_tts_provider,
    get_tts_router,
)


@pytest.fixture(autouse=True)
def _clear_caches() -> Generator[None, None, None]:
    from app.config import get_settings

    get_tts_provider.cache_clear()
    get_tts_router.cache_clear()
    get_settings.cache_clear()
    yield
    get_tts_provider.cache_clear()
    get_tts_router.cache_clear()
    get_settings.cache_clear()


# --------------------------------------------------------------------- #
# get_tts_provider                                                       #
# --------------------------------------------------------------------- #


def test_factory_returns_dummy_by_default() -> None:
    """Default `tts_backend=dummy` ships with the repo so tests + dev
    runs work offline."""
    assert isinstance(get_tts_provider(), DummyTTSProvider)


def test_factory_singleton_returns_same_instance() -> None:
    """Singleton via lru_cache so HTTP connection pools, log counters,
    and the WS-factory cache are shared."""
    assert get_tts_provider() is get_tts_provider()


def test_factory_return_type_satisfies_protocol() -> None:
    assert isinstance(get_tts_provider(), TTSProvider)


# --------------------------------------------------------------------- #
# get_tts_router                                                         #
# --------------------------------------------------------------------- #


def test_router_dummy_backend_has_single_provider_chain() -> None:
    """`tts_backend=dummy` makes a single-element chain — failover is
    impossible (and pointless) for a deterministic in-process synth.
    A1.2 expands the chain when Edge / Aliyun adapters land."""
    router = get_tts_router()
    assert isinstance(router, TTSRouter)
    assert len(router._chain) == 1
    assert isinstance(router._chain[0], DummyTTSProvider)


def test_router_singleton_returns_same_instance() -> None:
    assert get_tts_router() is get_tts_router()
