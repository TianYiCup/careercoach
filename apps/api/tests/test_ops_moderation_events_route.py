"""A-43: GET /v1/ops/moderation-events endpoint tests.

End-to-end via the real FastAPI app — the dep stack (X-Ops-Token
gate from A-41 + repo seam) is wired exactly as production runs it.
Tests inject an in-memory repo via `dependency_overrides` so each
test owns its data without lru_cache pollution.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta

import pytest
from app.config import get_settings
from app.main import app
from app.routes.v1.ops import _get_events_repo
from app.services.moderation_events import (
    InMemoryModerationEventRepository,
    ModerationEventRecord,
)
from httpx import ASGITransport, AsyncClient

_OPS_TOKEN = "test-ops-secret"
_HASH = "b" * 64


def _record(
    *,
    user_id: str = "u_demo",
    session_id: str | None = "s_001",
    context: str = "user_input",
    verdict: str = "allow",
    categories: tuple[str, ...] = (),
    score: float = 0.1,
    backend: str = "local_dict",
    trace_id: str = "trace_xxx",
    content_length: int = 64,
    created_at: datetime | None = None,
) -> ModerationEventRecord:
    return ModerationEventRecord(
        id=uuid.uuid4(),
        user_id=user_id,
        session_id=session_id,
        content_hash=_HASH,
        content_length=content_length,
        context=context,
        verdict=verdict,
        categories=categories,
        score=score,
        backend=backend,
        trace_id=trace_id,
        created_at=created_at or datetime.now(UTC),
    )


@pytest.fixture
async def _client(monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[AsyncClient]:
    """Configure ops token + clear settings cache for the duration
    of the test. The disabled-deployment branch builds its own
    client inside the test body because it needs the env var unset."""
    monkeypatch.setenv("OPS_API_TOKEN", _OPS_TOKEN)
    get_settings.cache_clear()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac
    get_settings.cache_clear()


@pytest.fixture
def _repo() -> InMemoryModerationEventRepository:
    """One repo per test, wired in via dependency_overrides so the
    process-wide `get_moderation_event_repository` lru_cache never
    sees it — clean isolation."""
    repo = InMemoryModerationEventRepository()
    app.dependency_overrides[_get_events_repo] = lambda: repo
    yield repo
    app.dependency_overrides.pop(_get_events_repo, None)


def _ops_headers() -> dict[str, str]:
    return {"X-Ops-Token": _OPS_TOKEN}


# --- happy path ---


async def test_returns_tail_newest_first(
    _client: AsyncClient, _repo: InMemoryModerationEventRepository
) -> None:
    now = datetime.now(UTC)
    await _repo.insert(_record(trace_id="t_old", created_at=now - timedelta(hours=3)))
    await _repo.insert(_record(trace_id="t_mid", created_at=now - timedelta(hours=1)))
    await _repo.insert(_record(trace_id="t_new", created_at=now - timedelta(minutes=5)))

    resp = await _client.get("/v1/ops/moderation-events", headers=_ops_headers())
    assert resp.status_code == 200

    body = resp.json()
    assert body["count"] == 3
    assert body["limit"] == 50
    assert [e["trace_id"] for e in body["events"]] == ["t_new", "t_mid", "t_old"]
    # Filters echo as null when none were applied.
    assert body["user_id"] is None
    assert body["verdict"] is None
    assert body["since"] is None
    assert body["until"] is None
    assert body["generated_at"] is not None


async def test_event_entry_includes_only_hash_not_raw_content(
    _client: AsyncClient, _repo: InMemoryModerationEventRepository
) -> None:
    """The privacy invariant pinned at the wire layer: the response
    must surface `content_hash` + `content_length` and NOT `content`.
    A future regression that piped raw text through would change
    this test, surfacing the leak in review."""
    await _repo.insert(_record(content_length=128))

    resp = await _client.get("/v1/ops/moderation-events", headers=_ops_headers())
    entry = resp.json()["events"][0]

    assert entry["content_hash"] == _HASH
    assert entry["content_length"] == 128
    assert "content" not in entry


# --- filters ---


async def test_user_id_filter_drills_down(
    _client: AsyncClient, _repo: InMemoryModerationEventRepository
) -> None:
    """End-to-end pin of `WHERE user_id` — the repo-side test pins
    this against the InMemory implementation; this test pins it
    through the full route + dep stack so a future middleware
    change that strips/rewrites the param surfaces here."""
    await _repo.insert(_record(user_id="u_alice"))
    await _repo.insert(_record(user_id="u_bob"))
    await _repo.insert(_record(user_id="u_alice"))

    resp = await _client.get(
        "/v1/ops/moderation-events",
        params={"user_id": "u_alice"},
        headers=_ops_headers(),
    )
    body = resp.json()

    assert body["count"] == 2
    assert all(e["user_id"] == "u_alice" for e in body["events"])
    assert body["user_id"] == "u_alice"  # echoed back


async def test_verdict_filter_narrows_results(
    _client: AsyncClient, _repo: InMemoryModerationEventRepository
) -> None:
    await _repo.insert(_record(verdict="allow", trace_id="t_allow"))
    await _repo.insert(_record(verdict="block", trace_id="t_block"))
    await _repo.insert(_record(verdict="redirect", trace_id="t_redirect"))

    resp = await _client.get(
        "/v1/ops/moderation-events",
        params={"verdict": "block"},
        headers=_ops_headers(),
    )
    body = resp.json()

    assert body["count"] == 1
    assert body["events"][0]["trace_id"] == "t_block"
    assert body["verdict"] == "block"


async def test_since_until_window_filters_apply(
    _client: AsyncClient, _repo: InMemoryModerationEventRepository
) -> None:
    """Half-open `[since, until)` matches the cost-rollup semantics
    so consecutive ops pages can be chained without overlap."""
    now = datetime.now(UTC)
    await _repo.insert(_record(trace_id="t_old", created_at=now - timedelta(days=2)))
    await _repo.insert(_record(trace_id="t_inside", created_at=now - timedelta(hours=12)))
    await _repo.insert(_record(trace_id="t_future", created_at=now + timedelta(hours=1)))

    resp = await _client.get(
        "/v1/ops/moderation-events",
        params={
            "since": (now - timedelta(days=1)).isoformat(),
            "until": now.isoformat(),
        },
        headers=_ops_headers(),
    )
    body = resp.json()

    assert body["count"] == 1
    assert body["events"][0]["trace_id"] == "t_inside"
    # Echo back so a downstream cache key can be derived from the
    # response alone.
    assert body["since"] is not None
    assert body["until"] is not None


# --- limit + cap ---


async def test_limit_param_caps_returned_rows(
    _client: AsyncClient, _repo: InMemoryModerationEventRepository
) -> None:
    now = datetime.now(UTC)
    for i in range(20):
        await _repo.insert(_record(trace_id=f"t_{i}", created_at=now - timedelta(seconds=i)))

    resp = await _client.get(
        "/v1/ops/moderation-events",
        params={"limit": 5},
        headers=_ops_headers(),
    )
    body = resp.json()

    assert body["count"] == 5
    assert body["limit"] == 5
    # 5 newest, not 5 random — sort applied before the cap.
    assert [e["trace_id"] for e in body["events"]] == [f"t_{i}" for i in range(5)]


async def test_limit_over_max_returns_422(_client: AsyncClient) -> None:
    """`MAX_MODERATION_EVENTS_LIMIT` is the hard ceiling — pin so a
    bored caller can't ask for `limit=100000` and pull the entire
    audit log in one request."""
    resp = await _client.get(
        "/v1/ops/moderation-events",
        params={"limit": 5000},
        headers=_ops_headers(),
    )
    assert resp.status_code == 422


async def test_limit_below_one_returns_422(_client: AsyncClient) -> None:
    resp = await _client.get(
        "/v1/ops/moderation-events",
        params={"limit": 0},
        headers=_ops_headers(),
    )
    assert resp.status_code == 422


# --- validation ---


async def test_invalid_verdict_returns_422(_client: AsyncClient) -> None:
    """`verdict=approved` is outside the Literal enum — pin that we
    reject rather than silently coercing or treating as unfiltered."""
    resp = await _client.get(
        "/v1/ops/moderation-events",
        params={"verdict": "approved"},
        headers=_ops_headers(),
    )
    assert resp.status_code == 422


async def test_empty_user_id_returns_422(_client: AsyncClient) -> None:
    """`user_id=` with empty value violates `min_length=1` — pin so
    a future loosening doesn't accidentally make the filter a no-op
    when present-but-empty (which would silently aggregate across
    all users, the opposite of what 'filter to user X' implies)."""
    resp = await _client.get(
        "/v1/ops/moderation-events",
        params={"user_id": ""},
        headers=_ops_headers(),
    )
    assert resp.status_code == 422


# --- auth gate (A-41 plumbing pinned end-to-end) ---


async def test_missing_ops_token_returns_401(_client: AsyncClient) -> None:
    """Auth dep must fire BEFORE the query-param validation —
    otherwise a probe with garbage params would leak a 422 that
    confirms the endpoint exists. FastAPI runs dependencies first."""
    resp = await _client.get("/v1/ops/moderation-events")
    assert resp.status_code == 401
    assert resp.json()["code"] == "OPS_AUTH_REQUIRED"


async def test_wrong_ops_token_returns_401(_client: AsyncClient) -> None:
    resp = await _client.get(
        "/v1/ops/moderation-events",
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
                "/v1/ops/moderation-events",
                headers={"X-Ops-Token": "anything"},
            )
        assert resp.status_code == 503
        assert resp.json()["code"] == "OPS_DISABLED"
    finally:
        get_settings.cache_clear()


# --- empty state ---


async def test_empty_repo_returns_empty_events_with_200(
    _client: AsyncClient, _repo: InMemoryModerationEventRepository
) -> None:
    """No rows in the window → empty list + count=0 with 200. Pin
    so a future refactor doesn't accidentally 404 (which would
    confuse ops dashboards into thinking the endpoint is broken)."""
    resp = await _client.get(
        "/v1/ops/moderation-events",
        params={"user_id": "u_nobody"},
        headers=_ops_headers(),
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["count"] == 0
    assert body["events"] == []
