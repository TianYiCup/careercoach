"""Tests for the ASR DI factory.

A-17 ships only the `dummy` backend. The factory exists so a future
PR can add `aliyun` / `tencent` without rewiring callers — they keep
calling `get_asr_provider()` and the settings flip decides the impl.
"""

from __future__ import annotations

import pytest
from app.asr import (
    ASRProvider,
    DummyASRProvider,
    get_asr_provider,
)


@pytest.fixture(autouse=True)
def _clear_factory_cache() -> None:
    """The factory is `lru_cache`-decorated for production singleton
    behavior. Tests must clear it so a settings tweak from one test
    doesn't leak into the next."""
    get_asr_provider.cache_clear()


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
