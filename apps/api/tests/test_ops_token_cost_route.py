"""A-42: GET /v1/ops/token-cost endpoint tests.

End-to-end via the real FastAPI app — the dep stack (X-Ops-Token
gate from A-41, llm_calls repo from A-39) is wired exactly as
production runs it. Tests inject an in-memory repo via
`dependency_overrides` so each test owns its data without lru_cache
pollution.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta

import pytest
from app.config import get_settings
from app.main import app
from app.routes.v1.ops import _get_repo
from app.services.llm_calls import (
    InMemoryLLMCallRepository,
    LLMCallRecord,
)
from httpx import ASGITransport, AsyncClient

_OPS_TOKEN = "test-ops-secret"


def _record(
    *,
    user_id: str = "u_demo",
    surface: str = "sandbox",
    model: str = "deepseek-chat",
    prompt: int = 100,
    completion: int = 50,
    created_at: datetime | None = None,
    trace_id: str = "trace_xxx",
) -> LLMCallRecord:
    return LLMCallRecord(
        id=uuid.uuid4(),
        trace_id=trace_id,
        user_id=user_id,
        surface=surface,
        model=model,
        prompt_tokens=prompt,
        completion_tokens=completion,
        total_tokens=prompt + completion,
        created_at=created_at or datetime.now(UTC),
    )


@pytest.fixture
async def _client(monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[AsyncClient]:
    """Configure ops token + clear settings cache for the duration
    of the test. Tests that want the disabled-deployment branch
    override `OPS_API_TOKEN` to empty inside the body and re-clear."""
    monkeypatch.setenv("OPS_API_TOKEN", _OPS_TOKEN)
    get_settings.cache_clear()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac
    get_settings.cache_clear()


@pytest.fixture
def _repo() -> InMemoryLLMCallRepository:
    """One repo per test. Wired in via dependency_overrides so the
    process-wide `get_llm_call_repository` lru_cache never sees it
    — clean isolation."""
    repo = InMemoryLLMCallRepository()
    app.dependency_overrides[_get_repo] = lambda: repo
    yield repo
    app.dependency_overrides.pop(_get_repo, None)


def _ops_headers() -> dict[str, str]:
    return {"X-Ops-Token": _OPS_TOKEN}


# --- happy path ---


async def test_returns_rollup_with_totals_and_breakdowns(
    _client: AsyncClient, _repo: InMemoryLLMCallRepository
) -> None:
    await _repo.insert(_record(model="deepseek-chat", prompt=100, completion=50))
    await _repo.insert(_record(model="qwen-max", prompt=200, completion=100))

    resp = await _client.get(
        "/v1/ops/token-cost", params={"user_id": "u_demo"}, headers=_ops_headers()
    )
    assert resp.status_code == 200

    body = resp.json()
    assert body["user_id"] == "u_demo"
    assert body["window"] == "7d"
    assert body["totals"] == {
        "call_count": 2,
        "prompt_tokens": 300,
        "completion_tokens": 150,
        "total_tokens": 450,
    }
    # Sorted most-expensive first.
    by_model_keys = [e["key"] for e in body["by_model"]]
    assert by_model_keys == ["qwen-max", "deepseek-chat"]
    # `since` / `until` echo back so the renderer doesn't re-derive.
    assert body["since"] is not None
    assert body["until"] is not None
    assert body["generated_at"] is not None


async def test_default_window_is_7d(_client: AsyncClient, _repo: InMemoryLLMCallRepository) -> None:
    """Omitting `?window=` must default to 7d — that's the most
    common ops query and we don't want the URL to be verbose for
    the common case."""
    resp = await _client.get(
        "/v1/ops/token-cost", params={"user_id": "u_demo"}, headers=_ops_headers()
    )
    assert resp.status_code == 200
    assert resp.json()["window"] == "7d"


async def test_window_all_returns_null_bounds(
    _client: AsyncClient, _repo: InMemoryLLMCallRepository
) -> None:
    """`window=all` skips time bounds entirely; since/until come back
    null so an ops dashboard renders 'all time' honestly rather than
    showing a fake epoch."""
    await _repo.insert(_record())

    resp = await _client.get(
        "/v1/ops/token-cost",
        params={"user_id": "u_demo", "window": "all"},
        headers=_ops_headers(),
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["window"] == "all"
    assert body["since"] is None
    assert body["until"] is None


async def test_window_1d_filters_older_records(
    _client: AsyncClient, _repo: InMemoryLLMCallRepository
) -> None:
    """Pin that the window math actually happens at the route
    boundary — a regression that hardcoded since=None would silently
    return rows older than the window."""
    old = datetime.now(UTC) - timedelta(days=5)
    fresh = datetime.now(UTC) - timedelta(hours=1)
    await _repo.insert(_record(prompt=1000, completion=1000, created_at=old))
    await _repo.insert(_record(prompt=50, completion=25, created_at=fresh))

    resp = await _client.get(
        "/v1/ops/token-cost",
        params={"user_id": "u_demo", "window": "1d"},
        headers=_ops_headers(),
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["totals"]["call_count"] == 1
    assert body["totals"]["total_tokens"] == 75


async def test_empty_user_returns_zero_totals(
    _client: AsyncClient, _repo: InMemoryLLMCallRepository
) -> None:
    """No records → totals zero, breakdowns empty. The endpoint must
    render an honest empty state rather than 404'ing or returning
    a `None`-shaped body."""
    resp = await _client.get(
        "/v1/ops/token-cost",
        params={"user_id": "u_nobody"},
        headers=_ops_headers(),
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["totals"]["call_count"] == 0
    assert body["totals"]["total_tokens"] == 0
    assert body["by_model"] == []
    assert body["by_surface"] == []


async def test_cross_tenant_isolation(
    _client: AsyncClient, _repo: InMemoryLLMCallRepository
) -> None:
    """End-to-end pin of the WHERE user_id clause — the repo-side
    test (A-39) pins this against the in-memory implementation; this
    test pins it through the full route + dep stack so a future
    middleware change that strips/rewrites the query param surfaces
    here, not in production."""
    await _repo.insert(_record(user_id="u_alice", prompt=100, completion=50))
    await _repo.insert(_record(user_id="u_bob", prompt=999, completion=999))

    resp = await _client.get(
        "/v1/ops/token-cost",
        params={"user_id": "u_alice"},
        headers=_ops_headers(),
    )
    assert resp.status_code == 200
    assert resp.json()["totals"]["total_tokens"] == 150


# --- validation ---


async def test_missing_user_id_returns_422(_client: AsyncClient) -> None:
    resp = await _client.get("/v1/ops/token-cost", headers=_ops_headers())
    assert resp.status_code == 422


async def test_empty_user_id_returns_422(_client: AsyncClient) -> None:
    """`user_id=` with empty value violates the `min_length=1` query
    constraint — pin so a future loosening doesn't accidentally
    aggregate across all users."""
    resp = await _client.get("/v1/ops/token-cost", params={"user_id": ""}, headers=_ops_headers())
    assert resp.status_code == 422


async def test_invalid_window_returns_422(_client: AsyncClient) -> None:
    """`window=14d` is outside the enum — pin that we reject rather
    than silently coercing to a default."""
    resp = await _client.get(
        "/v1/ops/token-cost",
        params={"user_id": "u_demo", "window": "14d"},
        headers=_ops_headers(),
    )
    assert resp.status_code == 422


# --- auth gate (A-41 plumbing pinned end-to-end) ---


async def test_missing_ops_token_returns_401(_client: AsyncClient) -> None:
    """Auth dep must fire BEFORE the query-param validation —
    otherwise a probe with garbage params would still leak a 422
    that confirms the endpoint exists. FastAPI runs dependencies
    first, so 401 wins."""
    resp = await _client.get("/v1/ops/token-cost", params={"user_id": "u_demo"})
    assert resp.status_code == 401
    assert resp.json()["code"] == "OPS_AUTH_REQUIRED"


async def test_wrong_ops_token_returns_401(_client: AsyncClient) -> None:
    resp = await _client.get(
        "/v1/ops/token-cost",
        params={"user_id": "u_demo"},
        headers={"X-Ops-Token": "wrong"},
    )
    assert resp.status_code == 401
    assert resp.json()["code"] == "OPS_AUTH_REQUIRED"


async def test_disabled_deployment_returns_503(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When `OPS_API_TOKEN` is unset the endpoint must 503 regardless
    of whether the request carries a header. Builds its own client
    (the shared `_client` fixture pre-seeds the env var)."""
    monkeypatch.setenv("OPS_API_TOKEN", "")
    get_settings.cache_clear()
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            resp = await ac.get(
                "/v1/ops/token-cost",
                params={"user_id": "u_demo"},
                headers={"X-Ops-Token": "anything"},
            )
        assert resp.status_code == 503
        assert resp.json()["code"] == "OPS_DISABLED"
    finally:
        get_settings.cache_clear()


# --- breakdown sort order pinned end-to-end ---


async def test_by_surface_sorted_by_total_tokens_desc(
    _client: AsyncClient, _repo: InMemoryLLMCallRepository
) -> None:
    await _repo.insert(_record(surface="sandbox", prompt=50, completion=50))
    await _repo.insert(_record(surface="copilot", prompt=500, completion=500))
    await _repo.insert(_record(surface="review", prompt=5, completion=5))

    resp = await _client.get(
        "/v1/ops/token-cost", params={"user_id": "u_demo"}, headers=_ops_headers()
    )
    by_surface_keys = [e["key"] for e in resp.json()["by_surface"]]
    assert by_surface_keys == ["copilot", "sandbox", "review"]


async def test_breakdown_totals_equal_headline_totals(
    _client: AsyncClient, _repo: InMemoryLLMCallRepository
) -> None:
    """The headline cost number and the sum of any breakdown column
    must match — the rollup endpoint loses trust the moment a
    dashboard renders `total_tokens: 100` next to a by_model table
    summing to 95. Pinned at the route level so a future serializer
    rewrite that drops a row surfaces here."""
    await _repo.insert(_record(model="m1", surface="sandbox", prompt=10, completion=20))
    await _repo.insert(_record(model="m2", surface="copilot", prompt=30, completion=40))
    await _repo.insert(_record(model="m1", surface="review", prompt=5, completion=5))

    resp = await _client.get(
        "/v1/ops/token-cost", params={"user_id": "u_demo"}, headers=_ops_headers()
    )
    body = resp.json()
    headline = body["totals"]["total_tokens"]
    by_model_sum = sum(e["total_tokens"] for e in body["by_model"])
    by_surface_sum = sum(e["total_tokens"] for e in body["by_surface"])
    assert headline == by_model_sum == by_surface_sum == 110
