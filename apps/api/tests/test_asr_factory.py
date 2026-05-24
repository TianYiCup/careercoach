"""Tests for the ASR DI factory.

A-17 ships only the `dummy` backend. The factory exists so a future
PR can add `aliyun` / `tencent` without rewiring callers — they keep
calling `get_asr_provider()` and the settings flip decides the impl.
"""

from __future__ import annotations

from collections.abc import Generator

import pytest
from app.asr import (
    ASRProvider,
    DummyASRProvider,
    get_asr_provider,
)


@pytest.fixture(autouse=True)
def _clear_caches() -> Generator[None, None, None]:
    """Both the ASR factory AND the settings loader are `lru_cache`-d
    for production singleton behavior. Clear both before AND after
    each test — `setenv` updates the env but a stale cached
    `Settings` object will hide it, and a polluted cache survives
    into the next test file if we only clear before."""
    from app.config import get_settings

    get_asr_provider.cache_clear()
    get_settings.cache_clear()
    yield
    get_asr_provider.cache_clear()
    get_settings.cache_clear()


def test_factory_returns_dummy_by_default() -> None:
    """A-17 ships only the dummy backend; default settings must
    produce it. A future PR adds aliyun/tencent and flips the default."""
    provider = get_asr_provider()
    assert isinstance(provider, DummyASRProvider)


def test_factory_singleton_returns_same_instance() -> None:
    """Singleton via lru_cache so all callers share the same provider
    (HTTP connection pools, telemetry counters, etc.). Tests prove
    this contract — otherwise the cache_clear fixture above would be
    silently a no-op."""
    first = get_asr_provider()
    second = get_asr_provider()
    assert first is second


def test_factory_return_type_satisfies_protocol() -> None:
    """Callers depend on the `ASRProvider` Protocol structurally, not
    on the concrete `DummyASRProvider` class. Pin the conformance so
    a future swap to AliyunASRProvider doesn't require type changes
    at the call sites."""
    provider = get_asr_provider()
    assert isinstance(provider, ASRProvider)


# --- A-28: aliyun backend selection ---


def test_factory_returns_aliyun_when_backend_and_keys_set(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`asr_backend=aliyun` + all three credentials present must wire
    the real Aliyun adapter, not the dummy."""
    from app.asr import AliyunASRProvider
    from app.config import get_settings

    monkeypatch.setenv("ASR_BACKEND", "aliyun")
    monkeypatch.setenv("ALIYUN_ACCESS_KEY_ID", "ak-test")
    monkeypatch.setenv("ALIYUN_ACCESS_KEY_SECRET", "secret-test")
    monkeypatch.setenv("ALIYUN_ASR_APP_KEY", "app-test")
    get_settings.cache_clear()

    provider = get_asr_provider()
    assert isinstance(provider, AliyunASRProvider)


def test_factory_refuses_aliyun_when_ak_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Misconfigured prod must fail loudly at construction time.
    A silent fallback to dummy would let a deploy 'succeed' and then
    transcribe nothing in production."""
    from app.config import get_settings

    monkeypatch.setenv("ASR_BACKEND", "aliyun")
    monkeypatch.setenv("ALIYUN_ACCESS_KEY_ID", "")
    monkeypatch.setenv("ALIYUN_ACCESS_KEY_SECRET", "")
    monkeypatch.setenv("ALIYUN_ASR_APP_KEY", "app-test")
    get_settings.cache_clear()

    with pytest.raises(ValueError, match="ALIYUN_ACCESS_KEY_ID"):
        get_asr_provider()


def test_factory_refuses_aliyun_when_app_key_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Same loud-failure rule for the project-level NLS app key."""
    from app.config import get_settings

    monkeypatch.setenv("ASR_BACKEND", "aliyun")
    monkeypatch.setenv("ALIYUN_ACCESS_KEY_ID", "ak-test")
    monkeypatch.setenv("ALIYUN_ACCESS_KEY_SECRET", "secret-test")
    monkeypatch.setenv("ALIYUN_ASR_APP_KEY", "")
    get_settings.cache_clear()

    with pytest.raises(ValueError, match="ALIYUN_ASR_APP_KEY"):
        get_asr_provider()


# --- A2: whisper_cpp backend selection ---


def test_factory_returns_whisper_cpp_when_backend_and_url_set(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`asr_backend=whisper_cpp` + a base URL must wire the self-hosted
    HTTP adapter, not the dummy. The URL points to an in-cluster
    whisper-server deployment; tests don't dial it, the factory just
    builds the adapter."""
    from app.asr import WhisperCppASRProvider
    from app.config import get_settings

    monkeypatch.setenv("ASR_BACKEND", "whisper_cpp")
    monkeypatch.setenv("WHISPER_CPP_BASE_URL", "http://whisper.internal:8080")
    get_settings.cache_clear()

    provider = get_asr_provider()
    assert isinstance(provider, WhisperCppASRProvider)


def test_factory_refuses_whisper_cpp_when_base_url_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Flipping the backend to whisper_cpp without setting the URL
    must fail loudly — same posture the Aliyun branch uses. A silent
    fallback to dummy would let a privacy-mode deploy 'succeed' but
    actually leak audio to no transcriber at all."""
    from app.config import get_settings

    monkeypatch.setenv("ASR_BACKEND", "whisper_cpp")
    monkeypatch.setenv("WHISPER_CPP_BASE_URL", "")
    get_settings.cache_clear()

    with pytest.raises(ValueError, match="WHISPER_CPP_BASE_URL"):
        get_asr_provider()
