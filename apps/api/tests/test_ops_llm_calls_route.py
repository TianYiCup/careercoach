"""A-46: GET /v1/ops/llm-calls endpoint tests.

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


async def test_returns_tail_newest_first(
    _client: AsyncClient, _repo: InMemoryLLMCallRepository
) -> None:
    now = datetime.now(UTC)
    await _repo.insert(_record(trace_id="t_old", created_at=now - timedelta(hours=3)))
    await _repo.insert(_record(trace_id="t_mid", created_at=now - timedelta(hours=1)))
    await _repo.insert(_record(trace_id="t_new", created_at=now - timedelta(minutes=5)))

    resp = await _client.get("/v1/ops/llm-calls", headers=_ops_headers())
    assert resp.status_code == 200

    body = resp.json()
    assert body["count"] == 3
    assert body["limit"] == 50
    assert [c["trace_id"] for c in body["calls"]] == ["t_new", "t_mid", "t_old"]
    # Filters echo as null when none were applied.
    assert body["user_id"] is None
    assert body["surface"] is None
    assert body["model"] is None
    assert body["since"] is None
    assert body["until"] is None
    assert body["generated_at"] is not None


async def test_call_entry_surfaces_token_counts_and_identifiers(
    _client: AsyncClient, _repo: InMemoryLLMCallRepository
) -> None:
    """The drill-down purpose: ops sees the trace_id + token counts,
    and can jump to Langfuse via trace_id to inspect prompt/response.
    Pin the wire shape doesn't drop any cost-relevant column."""
    await _repo.insert(_record(prompt=300, completion=200, model="qwen-max", surface="copilot"))

    resp = await _client.get("/v1/ops/llm-calls", headers=_ops_headers())
    entry = resp.json()["calls"][0]

    assert entry["prompt_tokens"] == 300
    assert entry["completion_tokens"] == 200
    assert entry["total_tokens"] == 500
    assert entry["model"] == "qwen-max"
    assert entry["surface"] == "copilot"
    # `id` and `trace_id` are both present — both are needed to
    # cross-reference into Langfuse and the audit log.
    assert entry["id"]
    assert entry["trace_id"]


# --- filters ---


async def test_user_id_filter_drills_down(
    _client: AsyncClient, _repo: InMemoryLLMCallRepository
) -> None:
    """End-to-end pin of `WHERE user_id` — pin through the full
    route + dep stack so a middleware change that strips/rewrites
    the param surfaces here, not in production."""
    await _repo.insert(_record(user_id="u_alice"))
    await _repo.insert(_record(user_id="u_bob"))
    await _repo.insert(_record(user_id="u_alice"))

    resp = await _client.get(
        "/v1/ops/llm-calls",
        params={"user_id": "u_alice"},
        headers=_ops_headers(),
    )
    body = resp.json()

    assert body["count"] == 2
    assert all(c["user_id"] == "u_alice" for c in body["calls"])
    assert body["user_id"] == "u_alice"  # echoed back


async def test_surface_filter_narrows_results(
    _client: AsyncClient, _repo: InMemoryLLMCallRepository
) -> None:
    await _repo.insert(_record(surface="sandbox", trace_id="t_sb"))
    await _repo.insert(_record(surface="review", trace_id="t_rv"))
    await _repo.insert(_record(surface="copilot", trace_id="t_cp"))

    resp = await _client.get(
        "/v1/ops/llm-calls",
        params={"surface": "review"},
        headers=_ops_headers(),
    )
    body = resp.json()

    assert body["count"] == 1
    assert body["calls"][0]["trace_id"] == "t_rv"
    assert body["surface"] == "review"


async def test_model_filter_narrows_results(
    _client: AsyncClient, _repo: InMemoryLLMCallRepository
) -> None:
    """The "which calls used the expensive model" drill-down."""
    await _repo.insert(_record(model="deepseek-chat", trace_id="t_ds"))
    await _repo.insert(_record(model="qwen-max", trace_id="t_qw"))

    resp = await _client.get(
        "/v1/ops/llm-calls",
        params={"model": "qwen-max"},
        headers=_ops_headers(),
    )
    body = resp.json()

    assert body["count"] == 1
    assert body["calls"][0]["trace_id"] == "t_qw"
    assert body["model"] == "qwen-max"


async def test_since_until_window_filters_apply(
    _client: AsyncClient, _repo: InMemoryLLMCallRepository
) -> None:
    now = datetime.now(UTC)
    await _repo.insert(_record(trace_id="t_old", created_at=now - timedelta(days=2)))
    await _repo.insert(_record(trace_id="t_inside", created_at=now - timedelta(hours=12)))
    await _repo.insert(_record(trace_id="t_future", created_at=now + timedelta(hours=1)))

    resp = await _client.get(
        "/v1/ops/llm-calls",
        params={
            "since": (now - timedelta(days=1)).isoformat(),
            "until": now.isoformat(),
        },
        headers=_ops_headers(),
    )
    body = resp.json()

    assert body["count"] == 1
    assert body["calls"][0]["trace_id"] == "t_inside"
    assert body["since"] is not None
    assert body["until"] is not None


async def test_filters_compose_with_user_and_surface(
    _client: AsyncClient, _repo: InMemoryLLMCallRepository
) -> None:
    """End-to-end AND-composition pin."""
    await _repo.insert(_record(user_id="u_alice", surface="copilot", trace_id="t_match"))
    await _repo.insert(_record(user_id="u_alice", surface="sandbox", trace_id="t_surface_off"))
    await _repo.insert(_record(user_id="u_bob", surface="copilot", trace_id="t_user_off"))

    resp = await _client.get(
        "/v1/ops/llm-calls",
        params={"user_id": "u_alice", "surface": "copilot"},
        headers=_ops_headers(),
    )
    body = resp.json()

    assert body["count"] == 1
    assert body["calls"][0]["trace_id"] == "t_match"


# --- limit + cap ---


async def test_limit_param_caps_returned_rows(
    _client: AsyncClient, _repo: InMemoryLLMCallRepository
) -> None:
    now = datetime.now(UTC)
    for i in range(20):
        await _repo.insert(_record(trace_id=f"t_{i}", created_at=now - timedelta(seconds=i)))

    resp = await _client.get(
        "/v1/ops/llm-calls",
        params={"limit": 5},
        headers=_ops_headers(),
    )
    body = resp.json()

    assert body["count"] == 5
    assert body["limit"] == 5
    # 5 newest, not 5 random.
    assert [c["trace_id"] for c in body["calls"]] == [f"t_{i}" for i in range(5)]


async def test_limit_over_max_returns_422(_client: AsyncClient) -> None:
    """`MAX_LLM_CALLS_LIMIT` is the hard ceiling — pin so a bored
    caller can't ask for `limit=100000` and pull the entire history
    in one request."""
    resp = await _client.get(
        "/v1/ops/llm-calls",
        params={"limit": 5000},
        headers=_ops_headers(),
    )
    assert resp.status_code == 422


async def test_limit_below_one_returns_422(_client: AsyncClient) -> None:
    resp = await _client.get(
        "/v1/ops/llm-calls",
        params={"limit": 0},
        headers=_ops_headers(),
    )
    assert resp.status_code == 422


# --- validation ---


async def test_invalid_surface_returns_422(_client: AsyncClient) -> None:
    """`surface=marketing` is outside the Literal enum — pin that we
    reject rather than silently treating as unfiltered. A future
    surface addition is a deliberate schema update, not an accident."""
    resp = await _client.get(
        "/v1/ops/llm-calls",
        params={"surface": "marketing"},
        headers=_ops_headers(),
    )
    assert resp.status_code == 422


async def test_empty_user_id_returns_422(_client: AsyncClient) -> None:
    """`user_id=` empty violates `min_length=1` — same pin as the
    other ops endpoints (prevents accidental all-users drill-down)."""
    resp = await _client.get(
        "/v1/ops/llm-calls",
        params={"user_id": ""},
        headers=_ops_headers(),
    )
    assert resp.status_code == 422


async def test_empty_model_returns_422(_client: AsyncClient) -> None:
    resp = await _client.get(
        "/v1/ops/llm-calls",
        params={"model": ""},
        headers=_ops_headers(),
    )
    assert resp.status_code == 422


# --- auth gate (A-41 plumbing pinned end-to-end) ---


async def test_missing_ops_token_returns_401(_client: AsyncClient) -> None:
    """Auth dep must fire BEFORE the query-param validation — same
    pin as every other ops endpoint, so a probe can't distinguish
    endpoint-exists vs bad-params."""
    resp = await _client.get("/v1/ops/llm-calls")
    assert resp.status_code == 401
    assert resp.json()["code"] == "OPS_AUTH_REQUIRED"


async def test_wrong_ops_token_returns_401(_client: AsyncClient) -> None:
    resp = await _client.get(
        "/v1/ops/llm-calls",
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
                "/v1/ops/llm-calls",
                headers={"X-Ops-Token": "anything"},
            )
        assert resp.status_code == 503
        assert resp.json()["code"] == "OPS_DISABLED"
    finally:
        get_settings.cache_clear()


# --- empty state ---


async def test_empty_repo_returns_empty_calls_with_200(
    _client: AsyncClient, _repo: InMemoryLLMCallRepository
) -> None:
    """No rows → empty list + count=0 with 200. Pin so a future
    refactor doesn't accidentally 404 (which would confuse the
    dashboard into thinking the endpoint is broken)."""
    resp = await _client.get(
        "/v1/ops/llm-calls",
        params={"user_id": "u_nobody"},
        headers=_ops_headers(),
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["count"] == 0
    assert body["calls"] == []
