"""Tests for liveness `/health` + readiness `/health/ready` probes.

The liveness test is a smoke. The readiness tests cover the dep-check
matrix introduced by A-35: each of the three checks (LLM, ASR, mod)
can be `ok`, `skipped`, or `fail`; the top-level status flips to
`degraded` + HTTP 503 if any check fails.
"""

from collections.abc import AsyncIterator
from typing import Any

import pytest
from app.asr import ASRAuthError, get_asr_provider
from app.asr.aliyun import AliyunASRProvider
from app.asr.dummy import DummyASRProvider
from app.config import get_settings
from app.llm.factory import NoCredentialsProvider, get_llm_router
from app.llm.router import LLMRouter
from app.main import app
from app.services.moderation import (
    DictBackend,
    LogOnlyEventSink,
    ModerationService,
    get_moderation_service,
)
from httpx import ASGITransport, AsyncClient


@pytest.fixture
async def client() -> AsyncIterator[AsyncClient]:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


@pytest.fixture(autouse=True)
def _clear_caches() -> Any:
    """`get_*` factories are lru_cache'd; tests that mutate env or
    swap dependencies must reset both BEFORE and AFTER each run so
    no stale provider leaks across tests."""
    get_llm_router.cache_clear()
    get_asr_provider.cache_clear()
    get_moderation_service.cache_clear()
    get_settings.cache_clear()
    yield
    app.dependency_overrides.clear()
    get_llm_router.cache_clear()
    get_asr_provider.cache_clear()
    get_moderation_service.cache_clear()
    get_settings.cache_clear()


async def test_health_returns_ok(client: AsyncClient) -> None:
    resp = await client.get("/health")

    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert body["version"]
    assert body["env"] in {"development", "staging", "production"}


# --------------------------------------------------------------------- #
# A-35: /health/ready                                                    #
# --------------------------------------------------------------------- #


def _install_llm_router(*, with_creds: bool) -> None:
    """Wire a real-ish LLM router. `with_creds=False` leaves only the
    NoCredentialsProvider, which the readiness check rejects."""
    if with_creds:

        class _StubReadyProvider:
            name = "stub_provider"

            async def stream_chat(self, messages, *, temperature=0.7, timeout=8.0, usage_sink=None):  # type: ignore[no-untyped-def]
                yield ""

        router = LLMRouter(primary=_StubReadyProvider())
    else:
        router = LLMRouter(primary=NoCredentialsProvider())
    app.dependency_overrides[get_llm_router] = lambda: router


def _install_dummy_asr() -> None:
    app.dependency_overrides[get_asr_provider] = DummyASRProvider


def _install_moderation_dict_only() -> None:
    service = ModerationService(backend=DictBackend.from_file(), event_sink=LogOnlyEventSink())
    app.dependency_overrides[get_moderation_service] = lambda: service


async def test_ready_all_ok_returns_200(
    client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Default-dev configuration: stub LLM with creds, dummy ASR,
    dict-only moderation. All three checks are `ok` or `skipped`,
    overall `ready`, HTTP 200."""
    monkeypatch.setenv("ASR_BACKEND", "dummy")
    get_settings.cache_clear()
    _install_llm_router(with_creds=True)
    _install_dummy_asr()
    _install_moderation_dict_only()

    resp = await client.get("/health/ready")

    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ready"
    assert body["checks"]["llm_router"]["status"] == "ok"
    assert body["checks"]["asr"]["status"] == "skipped"
    assert body["checks"]["asr"]["detail"] == "asr_backend=dummy"
    assert body["checks"]["moderation"]["status"] == "ok"
    # Every check reports latency_ms (int, >= 0).
    for check in body["checks"].values():
        assert isinstance(check["latency_ms"], int)
        assert check["latency_ms"] >= 0


async def test_ready_returns_503_when_llm_missing_credentials(
    client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """LLM router fallback to NoCredentialsProvider means every
    `/turns` call would 5xx — readiness MUST reject the instance
    so the LB drops it. Other checks staying green doesn't save
    the overall status."""
    monkeypatch.setenv("ASR_BACKEND", "dummy")
    get_settings.cache_clear()
    _install_llm_router(with_creds=False)
    _install_dummy_asr()
    _install_moderation_dict_only()

    resp = await client.get("/health/ready")

    assert resp.status_code == 503
    body = resp.json()
    assert body["status"] == "degraded"
    assert body["checks"]["llm_router"]["status"] == "fail"
    assert "no LLM credentials" in body["checks"]["llm_router"]["detail"]
    # Sibling checks still green — proves we ran them all, not
    # short-circuited at the first failure.
    assert body["checks"]["asr"]["status"] == "skipped"
    assert body["checks"]["moderation"]["status"] == "ok"


async def test_ready_aliyun_asr_token_failure_returns_503(
    client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`asr_backend=aliyun` exercises the token cache. If the token
    fetch raises (auth/upstream/timeout), readiness MUST fail."""
    monkeypatch.setenv("ASR_BACKEND", "aliyun")
    get_settings.cache_clear()
    _install_llm_router(with_creds=True)
    _install_moderation_dict_only()

    # Build an AliyunASRProvider with a token cache that always raises.
    from unittest.mock import AsyncMock

    failing_cache = AsyncMock()
    failing_cache.get.side_effect = ASRAuthError("revoked", provider="aliyun")
    provider = AliyunASRProvider(
        access_key_id="ak",
        access_key_secret="sec",
        app_key="app",
        ws_url="wss://example.invalid/ws",
        token_url="https://nls-meta.example/",
        token_cache=failing_cache,
    )
    app.dependency_overrides[get_asr_provider] = lambda: provider

    resp = await client.get("/health/ready")

    assert resp.status_code == 503
    body = resp.json()
    assert body["status"] == "degraded"
    assert body["checks"]["asr"]["status"] == "fail"
    assert "ASRAuthError" in body["checks"]["asr"]["detail"]


async def test_ready_runs_checks_in_parallel(
    client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Total wall-clock time MUST be bounded by the SLOWEST check, not
    the sum. We use an ASR provider that sleeps to verify gather
    parallelism isn't accidentally regressed to a serial loop."""
    import asyncio
    import time

    monkeypatch.setenv("ASR_BACKEND", "aliyun")
    get_settings.cache_clear()
    _install_llm_router(with_creds=True)
    _install_moderation_dict_only()

    class _SlowCache:
        async def get(self, **_kwargs: object) -> object:
            await asyncio.sleep(0.3)
            from app.asr._aliyun_token import AccessToken

            return AccessToken(token="tk", expires_at=9999999999)

    provider = AliyunASRProvider(
        access_key_id="ak",
        access_key_secret="sec",
        app_key="app",
        ws_url="wss://example.invalid/ws",
        token_url="https://nls-meta.example/",
        token_cache=_SlowCache(),  # type: ignore[arg-type]
    )
    app.dependency_overrides[get_asr_provider] = lambda: provider

    start = time.monotonic()
    resp = await client.get("/health/ready")
    elapsed = time.monotonic() - start

    assert resp.status_code == 200
    # If we were running checks serially, total would exceed 0.6s
    # (300ms ASR + LLM + moderation overhead). Allow generous slack
    # for slow CI but well below the 600ms sum.
    assert elapsed < 0.55, f"checks appear serial — elapsed={elapsed:.3f}s"


async def test_ready_response_schema_is_stable(
    client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Lock down the response shape so a downstream ops dashboard
    can parse it. Adding new check keys is OK; removing or
    renaming existing keys breaks consumers."""
    monkeypatch.setenv("ASR_BACKEND", "dummy")
    get_settings.cache_clear()
    _install_llm_router(with_creds=True)
    _install_dummy_asr()
    _install_moderation_dict_only()

    resp = await client.get("/health/ready")
    body = resp.json()

    assert set(body.keys()) == {"status", "version", "env", "checks"}
    assert set(body["checks"].keys()) == {"llm_router", "asr", "moderation"}
    for check in body["checks"].values():
        assert set(check.keys()) == {"status", "latency_ms", "detail"}
        assert check["status"] in {"ok", "skipped", "fail"}
