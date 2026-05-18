"""A-44: GET /v1/ops/moderation-stats endpoint tests.

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
_HASH = "c" * 64


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
    monkeypatch.setenv("OPS_API_TOKEN", _OPS_TOKEN)
    get_settings.cache_clear()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac
    get_settings.cache_clear()


@pytest.fixture
def _repo() -> InMemoryModerationEventRepository:
    repo = InMemoryModerationEventRepository()
    app.dependency_overrides[_get_events_repo] = lambda: repo
    yield repo
    app.dependency_overrides.pop(_get_events_repo, None)


def _ops_headers() -> dict[str, str]:
    return {"X-Ops-Token": _OPS_TOKEN}


# --- happy path ---


async def test_returns_totals_and_all_breakdowns(
    _client: AsyncClient, _repo: InMemoryModerationEventRepository
) -> None:
    """Seed a realistic mix: 6 events across 3 verdicts, 2 contexts,
    2 categories, 2 backends. Pin every section of the response body."""
    await _repo.insert(_record(verdict="allow", context="user_input", backend="local_dict"))
    await _repo.insert(_record(verdict="allow", context="ai_output", backend="aliyun"))
    await _repo.insert(
        _record(
            verdict="warn",
            categories=("violence",),
            context="user_input",
            backend="aliyun",
        )
    )
    await _repo.insert(
        _record(
            verdict="block",
            categories=("self_harm", "violence"),
            context="user_input",
            backend="aliyun",
        )
    )
    await _repo.insert(
        _record(verdict="block", categories=("self_harm",), context="ai_output", backend="aliyun")
    )
    await _repo.insert(_record(verdict="redirect", context="user_input", backend="local_dict"))

    resp = await _client.get("/v1/ops/moderation-stats", headers=_ops_headers())
    assert resp.status_code == 200

    body = resp.json()
    assert body["totals"] == {
        "event_count": 6,
        "allow_count": 2,
        "warn_count": 1,
        "redirect_count": 1,
        "block_count": 2,
    }
    # by_verdict is always 4 entries in canonical order.
    assert [e["key"] for e in body["by_verdict"]] == ["allow", "warn", "redirect", "block"]
    # Categories double-count per-row tagging.
    cat_counts = {e["key"]: e["count"] for e in body["by_category"]}
    assert cat_counts == {"self_harm": 2, "violence": 2}
    # Backends + contexts each show up.
    backend_counts = {e["key"]: e["count"] for e in body["by_backend"]}
    assert backend_counts == {"aliyun": 4, "local_dict": 2}
    context_counts = {e["key"]: e["count"] for e in body["by_context"]}
    assert context_counts == {"user_input": 4, "ai_output": 2}

    assert body["user_id"] is None
    assert body["since"] is None
    assert body["until"] is None
    assert body["generated_at"] is not None


async def test_empty_repo_returns_zero_totals_and_full_verdict_breakdown(
    _client: AsyncClient, _repo: InMemoryModerationEventRepository
) -> None:
    """Empty window MUST 200 with zero totals + full 4-row by_verdict
    (zero counts) so a dashboard renders the same layout from day one."""
    resp = await _client.get("/v1/ops/moderation-stats", headers=_ops_headers())

    assert resp.status_code == 200
    body = resp.json()
    assert body["totals"]["event_count"] == 0
    assert len(body["by_verdict"]) == 4
    assert all(e["count"] == 0 for e in body["by_verdict"])
    assert body["by_context"] == []
    assert body["by_category"] == []
    assert body["by_backend"] == []


# --- invariants ---


async def test_by_verdict_canonical_order_pinned_end_to_end(
    _client: AsyncClient, _repo: InMemoryModerationEventRepository
) -> None:
    """Pin the (allow, warn, redirect, block) ordering through the
    full wire layer — a future refactor that swaps to count-desc
    sort would silently flip the dashboard column order, and only
    this end-to-end pin catches it."""
    await _repo.insert(_record(verdict="block"))
    await _repo.insert(_record(verdict="block"))
    await _repo.insert(_record(verdict="block"))
    await _repo.insert(_record(verdict="allow"))

    resp = await _client.get("/v1/ops/moderation-stats", headers=_ops_headers())
    keys = [e["key"] for e in resp.json()["by_verdict"]]

    assert keys == ["allow", "warn", "redirect", "block"]


async def test_totals_sum_equals_event_count(
    _client: AsyncClient, _repo: InMemoryModerationEventRepository
) -> None:
    """Headline invariant: event_count == allow + warn + redirect + block.
    Dashboards lose trust the moment these disagree."""
    await _repo.insert(_record(verdict="allow"))
    await _repo.insert(_record(verdict="warn"))
    await _repo.insert(_record(verdict="block"))
    await _repo.insert(_record(verdict="redirect"))
    await _repo.insert(_record(verdict="block"))

    resp = await _client.get("/v1/ops/moderation-stats", headers=_ops_headers())
    totals = resp.json()["totals"]

    summed = (
        totals["allow_count"]
        + totals["warn_count"]
        + totals["redirect_count"]
        + totals["block_count"]
    )
    assert totals["event_count"] == 5
    assert summed == 5


async def test_by_verdict_counts_match_totals(
    _client: AsyncClient, _repo: InMemoryModerationEventRepository
) -> None:
    """The four totals fields and the by_verdict entries are two views
    of the same data — pin they never diverge."""
    await _repo.insert(_record(verdict="allow"))
    await _repo.insert(_record(verdict="allow"))
    await _repo.insert(_record(verdict="block"))

    resp = await _client.get("/v1/ops/moderation-stats", headers=_ops_headers())
    body = resp.json()

    by_verdict = {e["key"]: e["count"] for e in body["by_verdict"]}
    assert by_verdict["allow"] == body["totals"]["allow_count"] == 2
    assert by_verdict["block"] == body["totals"]["block_count"] == 1


async def test_by_category_counts_each_tag_per_row(
    _client: AsyncClient, _repo: InMemoryModerationEventRepository
) -> None:
    """End-to-end pin of the double-counting policy."""
    await _repo.insert(_record(categories=("self_harm", "violence")))
    await _repo.insert(_record(categories=("self_harm",)))
    await _repo.insert(_record(categories=("violence",)))

    resp = await _client.get("/v1/ops/moderation-stats", headers=_ops_headers())
    counts = {e["key"]: e["count"] for e in resp.json()["by_category"]}

    assert counts == {"self_harm": 2, "violence": 2}


# --- filters ---


async def test_user_id_filter_drills_down(
    _client: AsyncClient, _repo: InMemoryModerationEventRepository
) -> None:
    await _repo.insert(_record(user_id="u_alice", verdict="block"))
    await _repo.insert(_record(user_id="u_alice", verdict="allow"))
    await _repo.insert(_record(user_id="u_bob", verdict="block"))
    await _repo.insert(_record(user_id="u_bob", verdict="block"))

    resp = await _client.get(
        "/v1/ops/moderation-stats",
        params={"user_id": "u_alice"},
        headers=_ops_headers(),
    )
    body = resp.json()

    assert body["totals"]["event_count"] == 2
    assert body["totals"]["allow_count"] == 1
    assert body["totals"]["block_count"] == 1
    assert body["user_id"] == "u_alice"  # echoed back


async def test_since_until_window_applied(
    _client: AsyncClient, _repo: InMemoryModerationEventRepository
) -> None:
    now = datetime.now(UTC)
    await _repo.insert(_record(verdict="block", created_at=now - timedelta(days=10)))
    await _repo.insert(_record(verdict="allow", created_at=now - timedelta(hours=12)))
    await _repo.insert(_record(verdict="warn", created_at=now + timedelta(hours=1)))

    resp = await _client.get(
        "/v1/ops/moderation-stats",
        params={
            "since": (now - timedelta(days=1)).isoformat(),
            "until": now.isoformat(),
        },
        headers=_ops_headers(),
    )
    body = resp.json()

    # Only the half-day-old allow row falls inside [now-1d, now).
    assert body["totals"]["event_count"] == 1
    assert body["totals"]["allow_count"] == 1
    assert body["since"] is not None
    assert body["until"] is not None


# --- validation ---


async def test_empty_user_id_returns_422(_client: AsyncClient) -> None:
    """`user_id=` empty violates `min_length=1`. Pin so a future
    loosening doesn't turn the per-user drill-down into an
    accidentally-tenant-wide stat (the worst-case wrong reading)."""
    resp = await _client.get(
        "/v1/ops/moderation-stats",
        params={"user_id": ""},
        headers=_ops_headers(),
    )
    assert resp.status_code == 422


# --- auth gate (A-41 plumbing pinned end-to-end) ---


async def test_missing_ops_token_returns_401(_client: AsyncClient) -> None:
    """Auth dep must fire BEFORE the query-param validation — same
    pin as /token-cost and /moderation-events so a probe can't
    distinguish endpoint-exists vs bad-params."""
    resp = await _client.get("/v1/ops/moderation-stats")
    assert resp.status_code == 401
    assert resp.json()["code"] == "OPS_AUTH_REQUIRED"


async def test_wrong_ops_token_returns_401(_client: AsyncClient) -> None:
    resp = await _client.get(
        "/v1/ops/moderation-stats",
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
                "/v1/ops/moderation-stats",
                headers={"X-Ops-Token": "anything"},
            )
        assert resp.status_code == 503
        assert resp.json()["code"] == "OPS_DISABLED"
    finally:
        get_settings.cache_clear()


# --- response shape pin (privacy invariant carried over) ---


async def test_stats_response_never_exposes_raw_content(
    _client: AsyncClient, _repo: InMemoryModerationEventRepository
) -> None:
    """The stats endpoint is row-agnostic by construction (only counts
    surface), but pin defensively so a future refactor that, say,
    inlines sample rows for context can't accidentally leak content."""
    await _repo.insert(_record())

    resp = await _client.get("/v1/ops/moderation-stats", headers=_ops_headers())
    body = resp.json()

    # No row-level field should appear at any depth — flatten and check.
    serialized = str(body)
    assert "content_hash" not in serialized
    assert "content" not in serialized  # also catches content_length / content_hash defensively
