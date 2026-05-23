"""Tests for the TTS DI factory + router wiring.

Two surfaces:
  * `get_tts_provider()` — process-wide primary singleton
  * `get_tts_router()`   — process-wide failover-aware router

Both `lru_cache`-d like the ASR factory; tests must clear the cache to
re-read mutated settings.

A1.1 shipped the dummy-only path; A1.3 (this revision) expands the
test set to cover `edge` / `aliyun` backend selection + the
edge-primary + aliyun-backup router wiring.
"""

from __future__ import annotations

from collections.abc import Generator

import pytest
from app.tts import (
    AliyunTTSProvider,
    DummyTTSProvider,
    EdgeTTSProvider,
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
# get_tts_provider — backend selection                                   #
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


def test_factory_returns_edge_when_backend_set(monkeypatch: pytest.MonkeyPatch) -> None:
    """Flipping `tts_backend=edge` must wire the real Edge adapter, not
    the dummy."""
    from app.config import get_settings

    monkeypatch.setenv("TTS_BACKEND", "edge")
    get_settings.cache_clear()

    assert isinstance(get_tts_provider(), EdgeTTSProvider)


def test_factory_returns_aliyun_when_backend_and_keys_set(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.config import get_settings

    monkeypatch.setenv("TTS_BACKEND", "aliyun")
    monkeypatch.setenv("ALIYUN_ACCESS_KEY_ID", "ak-test")
    monkeypatch.setenv("ALIYUN_ACCESS_KEY_SECRET", "secret-test")
    monkeypatch.setenv("ALIYUN_TTS_APP_KEY", "tts-app-test")
    get_settings.cache_clear()

    assert isinstance(get_tts_provider(), AliyunTTSProvider)


def test_factory_refuses_aliyun_when_ak_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    """Misconfigured prod must fail loudly at construction time, same
    rule the ASR factory uses."""
    from app.config import get_settings

    monkeypatch.setenv("TTS_BACKEND", "aliyun")
    monkeypatch.setenv("ALIYUN_ACCESS_KEY_ID", "")
    monkeypatch.setenv("ALIYUN_ACCESS_KEY_SECRET", "")
    monkeypatch.setenv("ALIYUN_TTS_APP_KEY", "tts-app-test")
    get_settings.cache_clear()

    with pytest.raises(ValueError, match="ALIYUN_ACCESS_KEY_ID"):
        get_tts_provider()


def test_factory_refuses_aliyun_when_app_key_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.config import get_settings

    monkeypatch.setenv("TTS_BACKEND", "aliyun")
    monkeypatch.setenv("ALIYUN_ACCESS_KEY_ID", "ak-test")
    monkeypatch.setenv("ALIYUN_ACCESS_KEY_SECRET", "secret-test")
    monkeypatch.setenv("ALIYUN_TTS_APP_KEY", "")
    get_settings.cache_clear()

    with pytest.raises(ValueError, match="ALIYUN_TTS_APP_KEY"):
        get_tts_provider()


# --------------------------------------------------------------------- #
# get_tts_router — primary + optional backup wiring                      #
# --------------------------------------------------------------------- #


def test_router_dummy_backend_has_no_backups() -> None:
    """`tts_backend=dummy` makes a single-element chain — failover is
    impossible (and pointless) for a deterministic in-process synth."""
    router = get_tts_router()
    assert isinstance(router, TTSRouter)
    assert len(router._chain) == 1
    assert isinstance(router._chain[0], DummyTTSProvider)


def test_router_edge_backend_skips_aliyun_when_no_keys(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`tts_backend=edge` without Aliyun keys → chain has only edge.
    A silent fallback to dummy would mask the missing-key
    configuration; logging is the only signal here."""
    from app.config import get_settings

    monkeypatch.setenv("TTS_BACKEND", "edge")
    monkeypatch.setenv("ALIYUN_ACCESS_KEY_ID", "")
    monkeypatch.setenv("ALIYUN_TTS_APP_KEY", "")
    get_settings.cache_clear()

    router = get_tts_router()
    assert [type(p) for p in router._chain] == [EdgeTTSProvider]


def test_router_edge_backend_appends_aliyun_when_keys_present(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`tts_backend=edge` AND Aliyun keys → 2-provider chain so the
    primary→backup failover is live."""
    from app.config import get_settings

    monkeypatch.setenv("TTS_BACKEND", "edge")
    monkeypatch.setenv("ALIYUN_ACCESS_KEY_ID", "ak-test")
    monkeypatch.setenv("ALIYUN_ACCESS_KEY_SECRET", "secret-test")
    monkeypatch.setenv("ALIYUN_TTS_APP_KEY", "tts-app-test")
    get_settings.cache_clear()

    router = get_tts_router()
    assert [type(p) for p in router._chain] == [EdgeTTSProvider, AliyunTTSProvider]


def test_router_singleton_returns_same_instance() -> None:
    assert get_tts_router() is get_tts_router()
