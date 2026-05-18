"""A-45: GET /v1/ops/token-cost-daily endpoint tests.

End-to-end via the real FastAPI app — the dep stack (X-Ops-Token
gate from A-41 + llm_calls repo seam from A-39) is wired exactly
as production runs it. Tests inject an in-memory repo via
`dependency_overrides[_get_repo]` so each test owns its data
without the lru_cache'd singleton repo seeing test rows.
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
    monkeypatch.setenv("OPS_API_TOKEN", _OPS_TOKEN)
    get_settings.cache_clear()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac
    get_settings.cache_clear()


@pytest.fixture
def _repo() -> InMemoryLLMCallRepository:
    repo = InMemoryLLMCallRepository()
    app.dependency_overrides[_get_repo] = lambda: repo
    yield repo
    app.dependency_overrides.pop(_get_repo, None)


def _ops_headers() -> dict[str, str]:
    return {"X-Ops-Token": _OPS_TOKEN}


# --- happy path ---


async def test_default_days_is_seven(
    _client: AsyncClient, _repo: InMemoryLLMCallRepository
) -> None:
    """Omitting `?days=` defaults to 7 — week view is the most
    common ops query."""
    resp = await _client.get(
        "/v1/ops/token-cost-daily",
        params={"user_id": "u_demo"},
        headers=_ops_headers(),
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["days"] == 7
    assert len(body["daily"]) == 7


async def test_returns_one_entry_per_day_in_chronological_order(
    _client: AsyncClient, _repo: InMemoryLLMCallRepository
) -> None:
    resp = await _client.get(
        "/v1/ops/token-cost-daily",
        params={"user_id": "u_demo", "days": 5},
        headers=_ops_headers(),
    )
    body = resp.json()

    assert len(body["daily"]) == 5
    days = [e["day"] for e in body["daily"]]
    assert days == sorted(days)  # chronological asc


async def test_days_param_controls_bucket_count(
    _client: AsyncClient, _repo: InMemoryLLMCallRepository
) -> None:
    for n in (1, 3, 14, 30):
        resp = await _client.get(
            "/v1/ops/token-cost-daily",
            params={"user_id": "u_demo", "days": n},
            headers=_ops_headers(),
        )
        body = resp.json()
        assert body["days"] == n
        assert len(body["daily"]) == n


async def test_today_is_always_the_last_bucket(
    _client: AsyncClient, _repo: InMemoryLLMCallRepository
) -> None:
    """The window ends at request time → the last bucket is today
    (partial). Pin so a regression doesn't accidentally shift the
    window back by a day (e.g. only-complete-days mode)."""
    today_utc = datetime.now(UTC).date()

    resp = await _client.get(
        "/v1/ops/token-cost-daily",
        params={"user_id": "u_demo", "days": 3},
        headers=_ops_headers(),
    )
    body = resp.json()

    last_day = body["daily"][-1]["day"]
    assert last_day == today_utc.isoformat()


async def test_empty_user_returns_all_zero_buckets(
    _client: AsyncClient, _repo: InMemoryLLMCallRepository
) -> None:
    """No data → 200 with N zero-totals entries. The chart line
    sits flat at zero, doesn't 404 or render as missing."""
    resp = await _client.get(
        "/v1/ops/token-cost-daily",
        params={"user_id": "u_nobody", "days": 7},
        headers=_ops_headers(),
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["totals"]["call_count"] == 0
    assert body["totals"]["total_tokens"] == 0
    assert len(body["daily"]) == 7
    for entry in body["daily"]:
        assert entry["totals"]["call_count"] == 0
        assert entry["totals"]["total_tokens"] == 0


# --- invariants ---


async def test_totals_equal_sum_of_daily_buckets(
    _client: AsyncClient, _repo: InMemoryLLMCallRepository
) -> None:
    """Headline invariant: `totals.total_tokens` MUST equal the sum
    of all daily entries. Dashboard loses trust the moment the
    chart sum disagrees with the headline number."""
    now = datetime.now(UTC)
    await _repo.insert(_record(prompt=100, completion=50, created_at=now - timedelta(days=2)))
    await _repo.insert(_record(prompt=200, completion=100, created_at=now - timedelta(days=1)))
    await _repo.insert(_record(prompt=30, completion=20, created_at=now - timedelta(hours=1)))

    resp = await _client.get(
        "/v1/ops/token-cost-daily",
        params={"user_id": "u_demo", "days": 7},
        headers=_ops_headers(),
    )
    body = resp.json()

    daily_sum_total = sum(e["totals"]["total_tokens"] for e in body["daily"])
    daily_sum_calls = sum(e["totals"]["call_count"] for e in body["daily"])
    assert body["totals"]["total_tokens"] == daily_sum_total == 500
    assert body["totals"]["call_count"] == daily_sum_calls == 3


async def test_calls_bucket_by_utc_date_not_local_date(
    _client: AsyncClient, _repo: InMemoryLLMCallRepository
) -> None:
    """An insert at 2026-05-17 22:00 UTC must land in the 2026-05-17
    bucket — NOT in the local-time day (which would push it forward
    to 2026-05-18 in Asia/Shanghai). The PRD §0 contract is UTC for
    storage + bucketing; display-time translation is a render
    concern only."""
    target_day = datetime.now(UTC).date() - timedelta(days=1)
    late_evening_utc = datetime.combine(target_day, datetime.min.time(), tzinfo=UTC) + timedelta(
        hours=22
    )
    await _repo.insert(_record(prompt=100, completion=50, created_at=late_evening_utc))

    resp = await _client.get(
        "/v1/ops/token-cost-daily",
        params={"user_id": "u_demo", "days": 3},
        headers=_ops_headers(),
    )
    by_day = {e["day"]: e["totals"]["total_tokens"] for e in resp.json()["daily"]}

    assert by_day[target_day.isoformat()] == 150


async def test_cross_tenant_isolation(
    _client: AsyncClient, _repo: InMemoryLLMCallRepository
) -> None:
    """End-to-end pin of `WHERE user_id` at the time-series path."""
    now = datetime.now(UTC)
    await _repo.insert(_record(user_id="u_alice", prompt=100, completion=50, created_at=now))
    await _repo.insert(_record(user_id="u_bob", prompt=999, completion=999, created_at=now))

    resp = await _client.get(
        "/v1/ops/token-cost-daily",
        params={"user_id": "u_alice"},
        headers=_ops_headers(),
    )
    body = resp.json()

    assert body["totals"]["total_tokens"] == 150


# --- validation ---


async def test_missing_user_id_returns_422(_client: AsyncClient) -> None:
    resp = await _client.get("/v1/ops/token-cost-daily", headers=_ops_headers())
    assert resp.status_code == 422


async def test_empty_user_id_returns_422(_client: AsyncClient) -> None:
    """`user_id=` empty violates `min_length=1`. Pin so a future
    loosening doesn't accidentally aggregate across all users."""
    resp = await _client.get(
        "/v1/ops/token-cost-daily",
        params={"user_id": ""},
        headers=_ops_headers(),
    )
    assert resp.status_code == 422


async def test_days_zero_returns_422(_client: AsyncClient) -> None:
    """`days=0` is meaningless (no buckets) — pin the `ge=1` floor."""
    resp = await _client.get(
        "/v1/ops/token-cost-daily",
        params={"user_id": "u_demo", "days": 0},
        headers=_ops_headers(),
    )
    assert resp.status_code == 422


async def test_days_over_max_returns_422(_client: AsyncClient) -> None:
    """`MAX_TOKEN_COST_DAILY_DAYS` is the hard ceiling — pin so a
    bored caller can't ask for `days=10000` and bucket years of
    history in one request."""
    resp = await _client.get(
        "/v1/ops/token-cost-daily",
        params={"user_id": "u_demo", "days": 5000},
        headers=_ops_headers(),
    )
    assert resp.status_code == 422


# --- auth gate (A-41 plumbing pinned end-to-end) ---


async def test_missing_ops_token_returns_401(_client: AsyncClient) -> None:
    """Auth dep must fire BEFORE the query-param validation — same
    pin as every other ops endpoint."""
    resp = await _client.get("/v1/ops/token-cost-daily", params={"user_id": "u_demo"})
    assert resp.status_code == 401
    assert resp.json()["code"] == "OPS_AUTH_REQUIRED"


async def test_wrong_ops_token_returns_401(_client: AsyncClient) -> None:
    resp = await _client.get(
        "/v1/ops/token-cost-daily",
        params={"user_id": "u_demo"},
        headers={"X-Ops-Token": "wrong"},
    )
    assert resp.status_code == 401
    assert resp.json()["code"] == "OPS_AUTH_REQUIRED"


async def test_disabled_deployment_returns_503(monkeypatch: pytest.MonkeyPatch) -> None:
    """When `OPS_API_TOKEN` is unset the endpoint must 503 regardless
    of any header. Builds its own client because the shared `_client`
    fixture pre-seeds the env var."""
    monkeypatch.setenv("OPS_API_TOKEN", "")
    get_settings.cache_clear()
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            resp = await ac.get(
                "/v1/ops/token-cost-daily",
                params={"user_id": "u_demo"},
                headers={"X-Ops-Token": "anything"},
            )
        assert resp.status_code == 503
        assert resp.json()["code"] == "OPS_DISABLED"
    finally:
        get_settings.cache_clear()
