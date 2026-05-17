"""HTTP-layer tests for the 复盘师 (review) routes — POST + GET.

Production flow is asynchronous (A-13): `POST /v1/review/uploads`
returns immediately with `status="processing"` and a background
worker (driven by `ReviewWorkerQueue`) flips the row to `done` /
`failed` later. Clients poll `GET /v1/review/uploads/{id}`.

Test queue strategy
-------------------
Most tests inject `_SyncWorkerQueue` — it runs the work inline so the
POST returns the final state, which keeps the assertions readable.
That is *not lying* about production: the service code path is
identical (same `_process_upload`, same fold logic), just collapsed
into one event-loop tick.

Two dedicated tests use the real `InProcessWorkerQueue` to pin the
genuine async behavior:
  * `test_post_returns_processing_with_async_queue` — POST returns
    `processing` before the worker has even started
  * `test_async_queue_eventually_flips_to_done` — drain queue, then
    GET shows `done`

A `_DeferredQueue` test impl captures the work without running it so
the "processing" intermediate state can be asserted deterministically
without timing.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Awaitable, Callable
from datetime import UTC, datetime
from itertools import count

import pytest
from app.llm import Message, TokenUsage
from app.llm.errors import LLMUpstreamError
from app.main import app
from app.schemas.moderation import ModerationContext
from app.services.auth import mint_token
from app.services.moderation import (
    LogOnlyEventSink,
    ModerationBackendError,
    ModerationService,
)
from app.services.moderation.types import Decision
from app.services.review import (
    InMemoryReviewRepository,
    InProcessWorkerQueue,
    ReviewService,
    ReviewWorkerQueue,
    get_review_service,
)
from httpx import ASGITransport, AsyncClient

_SAMPLE_TEXT = "opponent: free this weekend?\nme: busy."

_REVIEWER_OUTPUT = (
    "TURN 0 | VERDICT: neutral\n"
    "TURN 1 | VERDICT: lose | REASON: too cold | BETTER: have plans, raincheck Tuesday?\n"
    "---\n"
    "SCORE: 6.4\n"
    "TOP_FAILURES: too curt; no warmth\n"
    "IMPROVEMENTS: offer alt slot; show empathy"
)


class _FakeProvider:
    """Returns a fixed string from `stream_chat`. Captures call count
    so a test can assert that LLM was actually called."""

    name = "fake"

    def __init__(self, output: str) -> None:
        self._output = output
        self.call_count = 0

    def stream_chat(
        self,
        messages: list[Message],
        *,
        temperature: float = 0.7,
        timeout: float = 8.0,
        usage_sink: list[TokenUsage] | None = None,
    ) -> AsyncIterator[str]:
        _ = usage_sink
        self.call_count += 1
        return self._chunks()

    async def _chunks(self) -> AsyncIterator[str]:
        yield self._output


class _RaisingProvider:
    """Always raises `LLMUpstreamError`. Used to pin the failed-status
    flow without a real network round-trip."""

    name = "raising"

    def stream_chat(
        self,
        messages: list[Message],
        *,
        temperature: float = 0.7,
        timeout: float = 8.0,
        usage_sink: list[TokenUsage] | None = None,
    ) -> AsyncIterator[str]:
        _ = usage_sink
        return self._raise()

    async def _raise(self) -> AsyncIterator[str]:
        raise LLMUpstreamError("provider exploded", provider=self.name)
        yield ""  # pragma: no cover — keeps function an async generator


_ALLOW_DECISION = Decision(verdict="allow", score=0.05)


class _FakeModBackend:
    """Substring-keyed moderation backend.

    Each test installs `{trigger_substring: Decision}`; the first
    matching key wins. Anything that doesn't match returns
    `_ALLOW_DECISION`. Used through a real `ModerationService` so the
    minor-strictness post-processing path runs identically to prod.
    """

    name = "fake_mod"

    def __init__(self, decisions: dict[str, Decision] | None = None) -> None:
        self._decisions = decisions or {}
        self.calls: list[tuple[str, ModerationContext]] = []

    async def evaluate(self, content: str, context: ModerationContext) -> Decision:
        self.calls.append((content, context))
        for trigger, decision in self._decisions.items():
            if trigger in content:
                return decision
        return _ALLOW_DECISION


class _RaisingModBackend:
    """Always raises `ModerationBackendError` — used to pin the
    backend-outage paths separately for input vs output."""

    name = "raising_mod"

    async def evaluate(self, content: str, context: ModerationContext) -> Decision:
        raise ModerationBackendError("backend exploded", backend=self.name)


def _moderation(backend: object | None = None) -> ModerationService:
    """Real `ModerationService` wrapping a fake backend so the
    minor-strictness post-processor still runs."""
    return ModerationService(
        backend=backend or _FakeModBackend(),  # type: ignore[arg-type]
        event_sink=LogOnlyEventSink(),
    )


class _SyncWorkerQueue:
    """Runs enqueued work inline so tests can assert on the final
    persisted state without dealing with asyncio task scheduling.

    Use this when the test cares about the *result* of the pipeline
    (status flips, persisted turns, summary fields) — i.e. most of
    them. Use `_DeferredQueue` when the test cares about the
    intermediate `processing` state, and `InProcessWorkerQueue` for
    the real production behavior end-to-end.
    """

    name = "sync"

    async def enqueue(self, work: Callable[[], Awaitable[None]]) -> None:
        await work()

    async def wait_idle(self) -> None:
        return None


class _DeferredQueue:
    """Captures enqueued work without running it.

    Lets a test assert "POST returned processing because work hasn't
    run yet" deterministically — no `asyncio.sleep`, no flake. The
    test calls `await queue.run_pending()` to trigger the captured
    work and then asserts on the final state.
    """

    name = "deferred"

    def __init__(self) -> None:
        self._pending: list[Callable[[], Awaitable[None]]] = []

    async def enqueue(self, work: Callable[[], Awaitable[None]]) -> None:
        self._pending.append(work)

    async def wait_idle(self) -> None:
        return None

    async def run_pending(self) -> None:
        pending = list(self._pending)
        self._pending.clear()
        for factory in pending:
            await factory()


def _deterministic_service(
    provider: object,
    *,
    moderation: ModerationService | None = None,
    queue: ReviewWorkerQueue | None = None,
    langfuse_client: object | None = None,
) -> tuple[ReviewService, InMemoryReviewRepository]:
    """Build a service with deterministic id factory + clock so tests
    can assert on stable values without monkeypatching.

    Defaults `queue` to `_SyncWorkerQueue` so most tests can keep
    asserting on the final state directly. Tests that exercise the
    real async path override with `InProcessWorkerQueue()` or
    `_DeferredQueue()`.

    Defaults `langfuse_client=None` so existing tests run with trace
    instrumentation as a no-op — the Langfuse-aware tests below pass
    a `MagicMock` shaped like `Langfuse` to verify the trace calls.
    """
    repo = InMemoryReviewRepository()
    counter = count(1)
    clock_counter = count(0)
    service = ReviewService(
        repo=repo,
        provider=provider,  # type: ignore[arg-type]  # _FakeProvider satisfies LLMProvider structurally
        moderation=moderation or _moderation(),
        queue=queue or _SyncWorkerQueue(),
        langfuse_client=langfuse_client,
        id_factory=lambda: f"up_test{next(counter):010d}",
        clock=lambda: datetime(2026, 5, 16, 10, next(clock_counter), tzinfo=UTC),
    )
    return service, repo


@pytest.fixture
async def client() -> AsyncIterator[AsyncClient]:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac
    # Always clear overrides so one test doesn't leak into another.
    app.dependency_overrides.clear()


# --------------------------------------------------------------------- #
# POST /v1/review/uploads — gates                                         #
# --------------------------------------------------------------------- #


async def test_no_token_returns_401(client: AsyncClient) -> None:
    resp = await client.post("/v1/review/uploads", json={"text": _SAMPLE_TEXT})

    assert resp.status_code == 401
    assert resp.json()["code"] == "UNAUTHORIZED"


async def test_age_unset_returns_403_age_required(client: AsyncClient) -> None:
    token = mint_token(
        user_id="u_no_age",
        persona_type="intern",
        is_minor=False,
        age_set=False,
    )

    resp = await client.post(
        "/v1/review/uploads",
        headers={"Authorization": f"Bearer {token}"},
        json={"text": _SAMPLE_TEXT},
    )

    assert resp.status_code == 403
    assert resp.json()["code"] == "AGE_REQUIRED"


async def test_text_too_long_returns_422(client: AsyncClient) -> None:
    """PRD §3.3 US-C1 L4 caps text at 5000 chars; longer must 422
    before any handler logic runs."""
    token = mint_token(
        user_id="u_adult",
        persona_type="intern",
        is_minor=False,
        age_set=True,
    )

    resp = await client.post(
        "/v1/review/uploads",
        headers={"Authorization": f"Bearer {token}"},
        json={"text": "x" * 5001},
    )

    assert resp.status_code == 422


async def test_empty_text_returns_422(client: AsyncClient) -> None:
    token = mint_token(
        user_id="u_adult",
        persona_type="intern",
        is_minor=False,
        age_set=True,
    )

    resp = await client.post(
        "/v1/review/uploads",
        headers={"Authorization": f"Bearer {token}"},
        json={"text": ""},
    )

    assert resp.status_code == 422


# --------------------------------------------------------------------- #
# POST /v1/review/uploads — sync analysis                                 #
# --------------------------------------------------------------------- #


async def test_minor_with_age_set_can_create_upload(client: AsyncClient) -> None:
    """Pin: minors must pass the review gate. The route's only block
    is `require_age_set`; `is_minor` does NOT exclude here. If a
    future change accidentally adds `require_adult` to the review
    route, this test red-lights it."""
    provider = _FakeProvider(_REVIEWER_OUTPUT)
    service, _ = _deterministic_service(provider)
    app.dependency_overrides[get_review_service] = lambda: service
    token = mint_token(
        user_id="u_minor",
        persona_type="in_school",
        is_minor=True,
        age_set=True,
    )

    resp = await client.post(
        "/v1/review/uploads",
        headers={"Authorization": f"Bearer {token}"},
        json={"text": _SAMPLE_TEXT},
    )

    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "done"
    assert body["upload_id"].startswith("up_test")


async def test_adult_post_returns_done_status_after_sync_analysis(client: AsyncClient) -> None:
    provider = _FakeProvider(_REVIEWER_OUTPUT)
    service, _ = _deterministic_service(provider)
    app.dependency_overrides[get_review_service] = lambda: service
    token = mint_token(
        user_id="u_adult",
        persona_type="intern",
        is_minor=False,
        age_set=True,
    )

    resp = await client.post(
        "/v1/review/uploads",
        headers={"Authorization": f"Bearer {token}"},
        json={"text": _SAMPLE_TEXT},
    )

    assert resp.status_code == 200
    assert resp.json() == {"upload_id": "up_test0000000001", "status": "done"}
    # The handler called the provider once (sync analyze_review).
    assert provider.call_count == 1


async def test_post_with_failing_llm_returns_failed_status(client: AsyncClient) -> None:
    """LLMError inside `analyze_review` must NOT 5xx the request — it
    must flip the upload to `failed` and still 200 so the client can
    GET the record and show the user a "couldn't analyse" UI."""
    service, _ = _deterministic_service(_RaisingProvider())
    app.dependency_overrides[get_review_service] = lambda: service
    token = mint_token(
        user_id="u_adult",
        persona_type="intern",
        is_minor=False,
        age_set=True,
    )

    resp = await client.post(
        "/v1/review/uploads",
        headers={"Authorization": f"Bearer {token}"},
        json={"text": _SAMPLE_TEXT},
    )

    assert resp.status_code == 200
    assert resp.json()["status"] == "failed"


# --------------------------------------------------------------------- #
# GET /v1/review/uploads/{upload_id}                                      #
# --------------------------------------------------------------------- #


async def test_get_unknown_upload_returns_404(client: AsyncClient) -> None:
    service, _ = _deterministic_service(_FakeProvider(_REVIEWER_OUTPUT))
    app.dependency_overrides[get_review_service] = lambda: service
    token = mint_token(
        user_id="u_adult",
        persona_type="intern",
        is_minor=False,
        age_set=True,
    )

    resp = await client.get(
        "/v1/review/uploads/up_does_not_exist",
        headers={"Authorization": f"Bearer {token}"},
    )

    assert resp.status_code == 404
    assert resp.json()["code"] == "NOT_FOUND"


async def test_get_returns_full_record_with_turns_and_summary(client: AsyncClient) -> None:
    """End-to-end: POST creates the record, GET returns the full
    analysed shape including turns + summary."""
    service, _ = _deterministic_service(_FakeProvider(_REVIEWER_OUTPUT))
    app.dependency_overrides[get_review_service] = lambda: service
    token = mint_token(
        user_id="u_adult",
        persona_type="intern",
        is_minor=False,
        age_set=True,
    )
    headers = {"Authorization": f"Bearer {token}"}

    post_resp = await client.post(
        "/v1/review/uploads",
        headers=headers,
        json={"text": _SAMPLE_TEXT},
    )
    upload_id = post_resp.json()["upload_id"]

    get_resp = await client.get(f"/v1/review/uploads/{upload_id}", headers=headers)

    assert get_resp.status_code == 200
    body = get_resp.json()
    assert body["upload_id"] == upload_id
    assert body["status"] == "done"
    assert body["summary"]["score"] == 6.4
    assert body["summary"]["top_failures"] == ["too curt", "no warmth"]
    assert body["summary"]["improvements"] == ["offer alt slot", "show empathy"]
    assert len(body["turns"]) == 2
    assert body["turns"][0] == {
        "turn_idx": 0,
        "speaker": "opponent",
        "content": "free this weekend?",
        "verdict": "neutral",
        "reason": None,
        "better": None,
    }
    assert body["turns"][1]["verdict"] == "lose"
    assert body["turns"][1]["reason"] == "too cold"
    assert body["turns"][1]["better"] == "have plans, raincheck Tuesday?"


async def test_get_failed_upload_returns_record_with_null_summary(client: AsyncClient) -> None:
    """`failed` uploads return their record with `summary = null` —
    the frontend keys "show summary card" off `summary != null`, so
    the contract is: `failed` ⇒ no summary even if summary_score
    somehow drifted into the row."""
    service, _ = _deterministic_service(_RaisingProvider())
    app.dependency_overrides[get_review_service] = lambda: service
    token = mint_token(
        user_id="u_adult",
        persona_type="intern",
        is_minor=False,
        age_set=True,
    )
    headers = {"Authorization": f"Bearer {token}"}

    post_resp = await client.post(
        "/v1/review/uploads", headers=headers, json={"text": _SAMPLE_TEXT}
    )
    upload_id = post_resp.json()["upload_id"]

    get_resp = await client.get(f"/v1/review/uploads/{upload_id}", headers=headers)

    assert get_resp.status_code == 200
    body = get_resp.json()
    assert body["status"] == "failed"
    assert body["summary"] is None


async def test_get_other_users_upload_returns_404_not_403(client: AsyncClient) -> None:
    """Cross-user reads must 404, not 403 — a probing client otherwise
    learns which upload_ids exist by status code. This is the route's
    enumerate-resistant ownership pattern."""
    service, _ = _deterministic_service(_FakeProvider(_REVIEWER_OUTPUT))
    app.dependency_overrides[get_review_service] = lambda: service
    owner_token = mint_token(
        user_id="u_owner",
        persona_type="intern",
        is_minor=False,
        age_set=True,
    )
    intruder_token = mint_token(
        user_id="u_intruder",
        persona_type="intern",
        is_minor=False,
        age_set=True,
    )

    post_resp = await client.post(
        "/v1/review/uploads",
        headers={"Authorization": f"Bearer {owner_token}"},
        json={"text": _SAMPLE_TEXT},
    )
    upload_id = post_resp.json()["upload_id"]

    get_resp = await client.get(
        f"/v1/review/uploads/{upload_id}",
        headers={"Authorization": f"Bearer {intruder_token}"},
    )

    assert get_resp.status_code == 404
    assert get_resp.json()["code"] == "NOT_FOUND"


async def test_get_no_token_returns_401(client: AsyncClient) -> None:
    resp = await client.get("/v1/review/uploads/up_anything")

    assert resp.status_code == 401


async def test_get_age_unset_returns_403_age_required(client: AsyncClient) -> None:
    token = mint_token(
        user_id="u_no_age",
        persona_type="intern",
        is_minor=False,
        age_set=False,
    )

    resp = await client.get(
        "/v1/review/uploads/up_anything",
        headers={"Authorization": f"Bearer {token}"},
    )

    assert resp.status_code == 403
    assert resp.json()["code"] == "AGE_REQUIRED"


# --------------------------------------------------------------------- #
# Moderation — input + output                                            #
# --------------------------------------------------------------------- #


async def test_input_block_returns_400_user_input_blocked(client: AsyncClient) -> None:
    """Red-line content in the upload text → 400 USER_INPUT_BLOCKED.
    The route surfaces categories so the frontend can render a
    targeted warning."""
    backend = _FakeModBackend(
        {
            "RED_LINE_TRIGGER": Decision(
                verdict="block",
                score=0.95,
                categories=("self_harm",),
            ),
        }
    )
    provider = _FakeProvider(_REVIEWER_OUTPUT)
    service, repo = _deterministic_service(provider, moderation=_moderation(backend))
    app.dependency_overrides[get_review_service] = lambda: service
    token = mint_token(
        user_id="u_adult",
        persona_type="intern",
        is_minor=False,
        age_set=True,
    )

    resp = await client.post(
        "/v1/review/uploads",
        headers={"Authorization": f"Bearer {token}"},
        json={"text": "this contains RED_LINE_TRIGGER content"},
    )

    assert resp.status_code == 400
    body = resp.json()
    assert body["code"] == "USER_INPUT_BLOCKED"
    assert "self_harm" in body["message"]
    # No upload row was written and the LLM was never called.
    assert provider.call_count == 0
    assert await repo.get("up_test0000000001") is None


async def test_input_warn_proceeds_normally_for_adult(client: AsyncClient) -> None:
    """`warn` is below the block threshold for adults — the upload
    proceeds (mirrors `TurnService` behavior)."""
    backend = _FakeModBackend(
        {
            "WARN_TRIGGER": Decision(
                verdict="warn",
                score=0.4,
                categories=("harassment",),
            ),
        }
    )
    provider = _FakeProvider(_REVIEWER_OUTPUT)
    service, _ = _deterministic_service(provider, moderation=_moderation(backend))
    app.dependency_overrides[get_review_service] = lambda: service
    token = mint_token(
        user_id="u_adult",
        persona_type="intern",
        is_minor=False,
        age_set=True,
    )

    resp = await client.post(
        "/v1/review/uploads",
        headers={"Authorization": f"Bearer {token}"},
        json={"text": "WARN_TRIGGER but should still be analysed"},
    )

    assert resp.status_code == 200
    assert resp.json()["status"] == "done"


async def test_input_warn_for_minor_promotes_to_block(client: AsyncClient) -> None:
    """PRD §3.0.5 C: minors get the strict tier — `warn` becomes `block`.
    Verified through the input gate (no upload row, no LLM call)."""
    backend = _FakeModBackend(
        {
            "WARN_TRIGGER": Decision(
                verdict="warn",
                score=0.4,
                categories=("harassment",),
            ),
        }
    )
    provider = _FakeProvider(_REVIEWER_OUTPUT)
    service, _ = _deterministic_service(provider, moderation=_moderation(backend))
    app.dependency_overrides[get_review_service] = lambda: service
    token = mint_token(
        user_id="u_minor",
        persona_type="in_school",
        is_minor=True,
        age_set=True,
    )

    resp = await client.post(
        "/v1/review/uploads",
        headers={"Authorization": f"Bearer {token}"},
        json={"text": "WARN_TRIGGER content from a minor"},
    )

    assert resp.status_code == 400
    assert resp.json()["code"] == "USER_INPUT_BLOCKED"
    assert provider.call_count == 0


async def test_output_block_flips_upload_to_failed(client: AsyncClient) -> None:
    """The LLM `better` field contains a red-line phrase; output
    moderation must drop the result and the upload status becomes
    `failed`. The 200 envelope still returns so the client can show
    a "couldn't analyse" UI."""
    # Reviewer output where the BETTER text trips the substring trigger.
    reviewer_with_red_line = (
        "TURN 0 | VERDICT: neutral\n"
        "TURN 1 | VERDICT: lose | REASON: too cold | BETTER: BAD_OUTPUT_PHRASE here\n"
        "---\n"
        "SCORE: 6.4\n"
        "TOP_FAILURES: a\n"
        "IMPROVEMENTS: b"
    )
    backend = _FakeModBackend(
        {
            "BAD_OUTPUT_PHRASE": Decision(
                verdict="block",
                score=0.92,
                categories=("violence",),
            ),
        }
    )
    provider = _FakeProvider(reviewer_with_red_line)
    service, _ = _deterministic_service(provider, moderation=_moderation(backend))
    app.dependency_overrides[get_review_service] = lambda: service
    token = mint_token(
        user_id="u_adult",
        persona_type="intern",
        is_minor=False,
        age_set=True,
    )
    headers = {"Authorization": f"Bearer {token}"}

    post_resp = await client.post(
        "/v1/review/uploads", headers=headers, json={"text": _SAMPLE_TEXT}
    )

    assert post_resp.status_code == 200
    upload_id = post_resp.json()["upload_id"]
    assert post_resp.json()["status"] == "failed"

    # Detail GET also reports failed + null summary; the unsafe `better`
    # text was never persisted (mark_failed leaves turns empty).
    get_resp = await client.get(f"/v1/review/uploads/{upload_id}", headers=headers)
    assert get_resp.status_code == 200
    body = get_resp.json()
    assert body["status"] == "failed"
    assert body["summary"] is None
    assert body["turns"] == []


async def test_output_redirect_also_flips_to_failed(client: AsyncClient) -> None:
    """`redirect` carries a crisis-line resource — for review output
    that means "this conversation is not the right thing for us to
    coach on". Drop the result, mark failed."""
    reviewer_with_redirect = (
        "TURN 0 | VERDICT: neutral\n"
        "TURN 1 | VERDICT: lose | REASON: REDIRECT_PHRASE here | BETTER: x\n"
        "---\n"
        "SCORE: 5.0\n"
        "TOP_FAILURES: a\n"
        "IMPROVEMENTS: b"
    )
    backend = _FakeModBackend(
        {
            "REDIRECT_PHRASE": Decision(
                verdict="redirect",
                score=0.88,
                categories=("self_harm",),
                redirect_resource={
                    "title": "心理援助 24h 热线",
                    "url": "tel:010-82951332",
                },  # type: ignore[arg-type]
            ),
        }
    )
    provider = _FakeProvider(reviewer_with_redirect)
    service, _ = _deterministic_service(provider, moderation=_moderation(backend))
    app.dependency_overrides[get_review_service] = lambda: service
    token = mint_token(
        user_id="u_adult",
        persona_type="intern",
        is_minor=False,
        age_set=True,
    )

    resp = await client.post(
        "/v1/review/uploads",
        headers={"Authorization": f"Bearer {token}"},
        json={"text": _SAMPLE_TEXT},
    )

    assert resp.status_code == 200
    assert resp.json()["status"] == "failed"


async def test_output_moderation_backend_error_does_not_lose_analysis(
    client: AsyncClient,
) -> None:
    """If the moderation backend errors during the OUTPUT pass, the
    user's already-paid-for analysis must not be invalidated. Service
    catches `ModerationBackendError` only on the output side and
    treats it as `allow`. (Input-side backend errors stay uncaught
    and would 5xx — verified by `test_input_moderation_backend_error_propagates`.)
    """
    backend = _RaisingModBackend()

    # Use a moderation service that lets the input call through allow but
    # raises on the output call. We need different behavior per call
    # — use a backend that allows the input text and raises on the LLM
    # output text. The simplest split is: substring-keyed allow on a
    # known input, raise on anything else (i.e. the LLM output).
    class _SplitBackend:
        name = "split_mod"

        async def evaluate(self, content: str, context: ModerationContext) -> Decision:
            if context == "user_input":
                return _ALLOW_DECISION
            raise ModerationBackendError("output mod down", backend=self.name)

    _ = backend  # silence unused
    provider = _FakeProvider(_REVIEWER_OUTPUT)
    service, _ = _deterministic_service(provider, moderation=_moderation(_SplitBackend()))
    app.dependency_overrides[get_review_service] = lambda: service
    token = mint_token(
        user_id="u_adult",
        persona_type="intern",
        is_minor=False,
        age_set=True,
    )

    resp = await client.post(
        "/v1/review/uploads",
        headers={"Authorization": f"Bearer {token}"},
        json={"text": _SAMPLE_TEXT},
    )

    # Output moderation outage is degraded gracefully — analysis lands.
    assert resp.status_code == 200
    assert resp.json()["status"] == "done"


async def test_input_moderation_backend_error_propagates(client: AsyncClient) -> None:
    """If the moderation backend errors on the INPUT side, the
    `ModerationBackendError` bubbles up unchanged — FastAPI returns 500
    in production and ops gets paged. Critically, NO orphan upload row
    is created (input mod runs before any persist) and the LLM is never
    called. Mirrors `TurnService.validate_turn_request`'s decision to
    fail loud instead of silently passing through unsafe content.

    The httpx ASGI transport re-raises unhandled exceptions in tests
    rather than mapping them to a 500 response, so we assert via
    `pytest.raises` rather than `resp.status_code`."""
    provider = _FakeProvider(_REVIEWER_OUTPUT)
    service, repo = _deterministic_service(provider, moderation=_moderation(_RaisingModBackend()))
    app.dependency_overrides[get_review_service] = lambda: service
    token = mint_token(
        user_id="u_adult",
        persona_type="intern",
        is_minor=False,
        age_set=True,
    )

    with pytest.raises(ModerationBackendError):
        await client.post(
            "/v1/review/uploads",
            headers={"Authorization": f"Bearer {token}"},
            json={"text": _SAMPLE_TEXT},
        )

    # No upload row leaked — input mod ran before persistence.
    assert await repo.get("up_test0000000001") is None
    assert provider.call_count == 0


# --------------------------------------------------------------------- #
# Async queue — actual production behavior                               #
# --------------------------------------------------------------------- #


async def test_post_returns_processing_with_deferred_queue(client: AsyncClient) -> None:
    """With a queue that hasn't run the work yet, POST returns the
    row in its initial `processing` state — and the LLM has not been
    called because the work was deferred.

    This is the *whole point* of A-13: the route returns immediately
    instead of blocking on the LLM round-trip + output moderation."""
    queue = _DeferredQueue()
    provider = _FakeProvider(_REVIEWER_OUTPUT)
    service, repo = _deterministic_service(provider, queue=queue)
    app.dependency_overrides[get_review_service] = lambda: service
    token = mint_token(
        user_id="u_adult",
        persona_type="intern",
        is_minor=False,
        age_set=True,
    )

    post_resp = await client.post(
        "/v1/review/uploads",
        headers={"Authorization": f"Bearer {token}"},
        json={"text": _SAMPLE_TEXT},
    )

    assert post_resp.status_code == 200
    body = post_resp.json()
    assert body["status"] == "processing"
    upload_id = body["upload_id"]

    # The work was enqueued but not run yet. LLM untouched. Row exists
    # in `processing` state.
    assert provider.call_count == 0
    record = await repo.get(upload_id)
    assert record is not None
    assert record.status == "processing"
    assert record.turns == ()
    assert record.summary_score is None


async def test_deferred_queue_flips_status_to_done_when_drained(client: AsyncClient) -> None:
    """The captured work, when run, drives the same `analyze_review`
    → output mod → fold pipeline as the sync queue. After drain,
    the GET reflects the final `done` state with the full record."""
    queue = _DeferredQueue()
    provider = _FakeProvider(_REVIEWER_OUTPUT)
    service, _ = _deterministic_service(provider, queue=queue)
    app.dependency_overrides[get_review_service] = lambda: service
    token = mint_token(
        user_id="u_adult",
        persona_type="intern",
        is_minor=False,
        age_set=True,
    )
    headers = {"Authorization": f"Bearer {token}"}

    post_resp = await client.post(
        "/v1/review/uploads", headers=headers, json={"text": _SAMPLE_TEXT}
    )
    upload_id = post_resp.json()["upload_id"]
    # Sanity: still processing before drain.
    assert post_resp.json()["status"] == "processing"

    await queue.run_pending()

    get_resp = await client.get(f"/v1/review/uploads/{upload_id}", headers=headers)
    assert get_resp.status_code == 200
    body = get_resp.json()
    assert body["status"] == "done"
    assert body["summary"]["score"] == 6.4
    assert len(body["turns"]) == 2


async def test_in_process_queue_eventually_runs_work(client: AsyncClient) -> None:
    """End-to-end with the real production queue: POST + wait_idle +
    GET sees the final state. Verifies the asyncio.create_task wiring
    actually runs the work — not just that the deferred-queue stand-in
    works."""
    queue = InProcessWorkerQueue()
    provider = _FakeProvider(_REVIEWER_OUTPUT)
    service, _ = _deterministic_service(provider, queue=queue)
    app.dependency_overrides[get_review_service] = lambda: service
    token = mint_token(
        user_id="u_adult",
        persona_type="intern",
        is_minor=False,
        age_set=True,
    )
    headers = {"Authorization": f"Bearer {token}"}

    post_resp = await client.post(
        "/v1/review/uploads", headers=headers, json={"text": _SAMPLE_TEXT}
    )
    upload_id = post_resp.json()["upload_id"]
    assert post_resp.json()["status"] == "processing"

    # Drain any in-flight background work spawned by the POST.
    await queue.wait_idle()

    get_resp = await client.get(f"/v1/review/uploads/{upload_id}", headers=headers)
    assert get_resp.status_code == 200
    assert get_resp.json()["status"] == "done"


async def test_in_process_queue_swallows_unexpected_worker_exception(client: AsyncClient) -> None:
    """If the worker crashes on something the service didn't catch
    (e.g. a moderation backend raising during the OUTPUT pass —
    actually we *do* catch that — so this test forces it via a backend
    that raises a non-`ModerationBackendError` exception), the row
    stays in `processing` and the asyncio task ends cleanly.

    The user-facing impact: GET reports `processing` indefinitely.
    The retry button on the frontend would let them try again — this
    is the documented worst-case for the in-process queue (foundation
    note: durability lands with the Redis migration in A-14+)."""

    class _UnexpectedlyExplodingBackend:
        name = "exploding_output"

        async def evaluate(self, content: str, context: ModerationContext) -> Decision:
            if context == "user_input":
                return _ALLOW_DECISION
            # Anything other than ModerationBackendError isn't caught
            # by the service — simulates a programmer error / library
            # crash in the moderation stack.
            raise RuntimeError("unexpected output-mod crash")

    queue = InProcessWorkerQueue()
    provider = _FakeProvider(_REVIEWER_OUTPUT)
    service, _ = _deterministic_service(
        provider,
        queue=queue,
        moderation=_moderation(_UnexpectedlyExplodingBackend()),
    )
    app.dependency_overrides[get_review_service] = lambda: service
    token = mint_token(
        user_id="u_adult",
        persona_type="intern",
        is_minor=False,
        age_set=True,
    )
    headers = {"Authorization": f"Bearer {token}"}

    post_resp = await client.post(
        "/v1/review/uploads", headers=headers, json={"text": _SAMPLE_TEXT}
    )
    assert post_resp.json()["status"] == "processing"
    upload_id = post_resp.json()["upload_id"]

    # `wait_idle` blocks until the task finishes — and it WILL finish
    # cleanly because `_run_with_logging` catches all exceptions. The
    # row remains in `processing`.
    await queue.wait_idle()

    get_resp = await client.get(f"/v1/review/uploads/{upload_id}", headers=headers)
    assert get_resp.status_code == 200
    assert get_resp.json()["status"] == "processing"


# --------------------------------------------------------------------- #
# Langfuse trace instrumentation (A-14)                                  #
# --------------------------------------------------------------------- #


def _mock_langfuse_client() -> tuple[object, object, object]:
    """Build a `MagicMock`-shaped Langfuse client that records calls.

    Mirrors the pattern in `test_sessions_turn_service.py` so the test
    contract stays uniform across `/turns` and `/review` traces.
    Returns (client, inner_trace, inner_generation) so callers can
    assert on each level of the call tree.
    """
    from unittest.mock import MagicMock

    client = MagicMock(name="langfuse")
    inner_trace = MagicMock(name="trace")
    inner_gen = MagicMock(name="generation")
    inner_trace.generation.return_value = inner_gen
    client.trace.return_value = inner_trace
    return client, inner_trace, inner_gen


async def test_review_emits_one_trace_per_upload_with_generation(
    client: AsyncClient,
) -> None:
    """The happy path produces one `review_upload` trace with one
    `analyze_review` generation, and the trace is finished with the
    upload's final status. This is what an analyst sees on the
    Langfuse UI."""
    langfuse, inner_trace, inner_gen = _mock_langfuse_client()
    provider = _FakeProvider(_REVIEWER_OUTPUT)
    service, _ = _deterministic_service(provider, langfuse_client=langfuse)
    app.dependency_overrides[get_review_service] = lambda: service
    token = mint_token(
        user_id="u_adult",
        persona_type="intern",
        is_minor=False,
        age_set=True,
    )

    resp = await client.post(
        "/v1/review/uploads",
        headers={"Authorization": f"Bearer {token}"},
        json={"text": _SAMPLE_TEXT},
    )
    assert resp.status_code == 200
    upload_id = resp.json()["upload_id"]

    # One top-level trace, named `review_upload`. `upload_id` lifts to
    # the Langfuse top-level `session_id` field (A-23) — used to be
    # in `input`. Initial tags are surface + minor (A-24) +
    # verdict_input (A-25).
    langfuse.trace.assert_called_once()  # type: ignore[attr-defined]
    _trace_args, trace_kwargs = langfuse.trace.call_args  # type: ignore[attr-defined]
    assert trace_kwargs["name"] == "review_upload"
    assert trace_kwargs["session_id"] == upload_id
    assert trace_kwargs["input"]["text_len"] == len(_SAMPLE_TEXT)
    assert trace_kwargs["metadata"]["user_id"] == "u_adult"
    assert trace_kwargs["metadata"]["is_minor"] is False
    assert trace_kwargs["tags"] == [
        "surface:review",
        "minor:false",
        "verdict_input:allow",
    ]

    # One generation under that trace — the analyze_review LLM call.
    inner_trace.generation.assert_called_once()  # type: ignore[attr-defined]
    _gen_args, gen_kwargs = inner_trace.generation.call_args  # type: ignore[attr-defined]
    assert gen_kwargs["name"] == "analyze_review"
    assert gen_kwargs["input"]["text_len"] == len(_SAMPLE_TEXT)

    # The generation `end` carries the parsed-result summary, NOT the
    # raw turn text (PII discipline — see `_summarize_result_for_trace`).
    inner_gen.end.assert_called_once()  # type: ignore[attr-defined]
    _end_args, end_kwargs = inner_gen.end.call_args  # type: ignore[attr-defined]
    assert end_kwargs["output"]["parsed"] is True
    assert end_kwargs["output"]["turn_count"] == 2
    assert end_kwargs["output"]["summary_score"] == 6.4

    # Trace finished with the final status payload.
    # `update` is called via `finish` — pull the output kwarg from the
    # last call (langfuse v2's StatefulTraceClient.update takes kwargs).
    update_calls = inner_trace.update.call_args_list  # type: ignore[attr-defined]
    assert update_calls, "trace.update was never called"
    final_call = update_calls[-1]
    assert final_call.kwargs["output"]["upload_id"] == upload_id
    assert final_call.kwargs["output"]["status"] == "done"
    assert final_call.kwargs["output"]["turn_count"] == 2


async def test_input_warn_verdict_tagged_on_trace(client: AsyncClient) -> None:
    """A-25: when input moderation returns `warn` (adult passes through),
    the trace's initial tags include `verdict_input:warn` so analysts
    can filter for the soft-flag uploads without joining the DB."""
    backend = _FakeModBackend(
        {
            "WARN_TRIGGER": Decision(
                verdict="warn",
                score=0.4,
                categories=("harassment",),
            ),
        }
    )
    langfuse, _trace, _gen = _mock_langfuse_client()
    provider = _FakeProvider(_REVIEWER_OUTPUT)
    service, _ = _deterministic_service(
        provider,
        moderation=_moderation(backend),
        langfuse_client=langfuse,
    )
    app.dependency_overrides[get_review_service] = lambda: service
    token = mint_token(
        user_id="u_adult",
        persona_type="intern",
        is_minor=False,
        age_set=True,
    )

    resp = await client.post(
        "/v1/review/uploads",
        headers={"Authorization": f"Bearer {token}"},
        json={"text": "WARN_TRIGGER but should still be analysed"},
    )

    assert resp.status_code == 200
    _, trace_kwargs = langfuse.trace.call_args  # type: ignore[attr-defined]
    assert "verdict_input:warn" in trace_kwargs["tags"]


async def test_output_block_adds_verdict_output_tag(client: AsyncClient) -> None:
    """A-25: when output moderation blocks the LLM-produced text,
    `verdict_output:block` is appended via add_tags so analysts can
    spot the rare "LLM said something bad" cases without joining
    against the review audit table."""
    # Reviewer output mirrors `test_output_block_flips_upload_to_failed`
    # so any parser quirk doesn't bleed in.
    reviewer_with_red_line = (
        "TURN 0 | VERDICT: neutral\n"
        "TURN 1 | VERDICT: lose | REASON: too cold | BETTER: BAD_OUTPUT_PHRASE here\n"
        "---\n"
        "SCORE: 6.4\n"
        "TOP_FAILURES: a\n"
        "IMPROVEMENTS: b"
    )
    backend = _FakeModBackend(
        {
            "BAD_OUTPUT_PHRASE": Decision(
                verdict="block",
                score=0.92,
                categories=("violence",),
            ),
        }
    )
    langfuse, inner_trace, _ = _mock_langfuse_client()
    provider = _FakeProvider(reviewer_with_red_line)
    service, _ = _deterministic_service(
        provider,
        moderation=_moderation(backend),
        langfuse_client=langfuse,
    )
    app.dependency_overrides[get_review_service] = lambda: service
    token = mint_token(
        user_id="u_adult",
        persona_type="intern",
        is_minor=False,
        age_set=True,
    )

    # Use _SAMPLE_TEXT (2 turns) so the reviewer parser keeps the
    # synthetic TURN 1 line — otherwise turn-count mismatch drops it
    # and BAD_OUTPUT_PHRASE never reaches the moderation flatten.
    resp = await client.post(
        "/v1/review/uploads",
        headers={"Authorization": f"Bearer {token}"},
        json={"text": _SAMPLE_TEXT},
    )

    # Upload completes with failed status (output dropped).
    assert resp.status_code == 200
    assert resp.json()["status"] == "failed"

    # The trace got a `verdict_output:block` tag via add_tags. Find
    # the tags= update call; the value re-sends the full union so
    # initial tags are still there.
    tag_calls = [
        c
        for c in inner_trace.update.call_args_list  # type: ignore[attr-defined]
        if "tags" in c.kwargs
    ]
    assert tag_calls, "expected at least one tags= update for the output verdict"
    final_tags = tag_calls[-1].kwargs["tags"]
    assert "verdict_output:block" in final_tags
    # Initial tags survive the union — A-24's merge semantics.
    assert "surface:review" in final_tags
    assert "verdict_input:allow" in final_tags


async def test_no_output_verdict_tag_when_backend_fails(client: AsyncClient) -> None:
    """A-25: when the output moderation backend itself raises, we
    treat-as-allow but explicitly DON'T tag `verdict_output:allow`
    — that would be misleading. The trace just shows the input tag
    and no output tag."""

    class _OutputOnlyRaisingBackend:
        name = "output_raising"

        async def evaluate(self, content: str, context: ModerationContext) -> Decision:
            if context == "user_input":
                return _ALLOW_DECISION
            raise ModerationBackendError("output mod down", backend=self.name)

    langfuse, inner_trace, _ = _mock_langfuse_client()
    provider = _FakeProvider(_REVIEWER_OUTPUT)
    service, _ = _deterministic_service(
        provider,
        moderation=_moderation(_OutputOnlyRaisingBackend()),
        langfuse_client=langfuse,
    )
    app.dependency_overrides[get_review_service] = lambda: service
    token = mint_token(
        user_id="u_adult",
        persona_type="intern",
        is_minor=False,
        age_set=True,
    )

    resp = await client.post(
        "/v1/review/uploads",
        headers={"Authorization": f"Bearer {token}"},
        json={"text": _SAMPLE_TEXT},
    )

    # Output survives despite backend failure (treat-as-allow).
    assert resp.status_code == 200
    assert resp.json()["status"] == "done"

    # But no verdict_output tag — backend never produced a decision.
    tag_calls = [
        c
        for c in inner_trace.update.call_args_list  # type: ignore[attr-defined]
        if "tags" in c.kwargs
    ]
    # Either no tag updates at all, or any that happened don't carry
    # a verdict_output: prefix.
    for call in tag_calls:
        for tag in call.kwargs["tags"]:
            assert not tag.startswith("verdict_output:"), (
                f"unexpected verdict_output tag when backend failed: {tag}"
            )


async def test_review_trace_marks_error_when_worker_crashes(client: AsyncClient) -> None:
    """An uncaught exception inside the worker (e.g. a moderation
    backend raising something other than `ModerationBackendError`)
    propagates through `_process_upload`'s outer try/except, which
    calls `trace.fail(exc)` to mark the Langfuse trace as ERROR
    before re-raising. The row stays in `processing`."""

    class _UnexpectedlyExplodingBackend:
        name = "exploding_output"

        async def evaluate(self, content: str, context: ModerationContext) -> Decision:
            if context == "user_input":
                return _ALLOW_DECISION
            raise RuntimeError("unexpected output-mod crash")

    langfuse, inner_trace, _ = _mock_langfuse_client()
    queue = InProcessWorkerQueue()
    provider = _FakeProvider(_REVIEWER_OUTPUT)
    service, _ = _deterministic_service(
        provider,
        queue=queue,
        moderation=_moderation(_UnexpectedlyExplodingBackend()),
        langfuse_client=langfuse,
    )
    app.dependency_overrides[get_review_service] = lambda: service
    token = mint_token(
        user_id="u_adult",
        persona_type="intern",
        is_minor=False,
        age_set=True,
    )

    resp = await client.post(
        "/v1/review/uploads",
        headers={"Authorization": f"Bearer {token}"},
        json={"text": _SAMPLE_TEXT},
    )
    assert resp.json()["status"] == "processing"

    # Worker runs in the background; await it before asserting.
    await queue.wait_idle()

    # Trace was opened and marked ERROR (via `trace.fail` → update).
    langfuse.trace.assert_called_once()  # type: ignore[attr-defined]
    update_calls = inner_trace.update.call_args_list  # type: ignore[attr-defined]
    error_calls = [c for c in update_calls if c.kwargs.get("level") == "ERROR"]
    assert error_calls, f"expected an update(level='ERROR'), got {update_calls!r}"
    assert "unexpected output-mod crash" in error_calls[0].kwargs["status_message"]


async def test_review_with_no_langfuse_client_works_unchanged(client: AsyncClient) -> None:
    """Sanity: when LANGFUSE_* keys are unset the service runs the
    full pipeline without touching anything trace-related. Existing
    24 tests already verify this implicitly (they all pass
    `langfuse_client=None`); this one makes the contract explicit."""
    provider = _FakeProvider(_REVIEWER_OUTPUT)
    service, _ = _deterministic_service(provider, langfuse_client=None)
    app.dependency_overrides[get_review_service] = lambda: service
    token = mint_token(
        user_id="u_adult",
        persona_type="intern",
        is_minor=False,
        age_set=True,
    )

    resp = await client.post(
        "/v1/review/uploads",
        headers={"Authorization": f"Bearer {token}"},
        json={"text": _SAMPLE_TEXT},
    )

    # No exceptions, full pipeline still ran (status flipped via the
    # sync queue), upload exists with the canonical record.
    assert resp.status_code == 200
    assert resp.json()["status"] == "done"


async def test_review_trace_records_failed_generation_on_llm_error(client: AsyncClient) -> None:
    """When `analyze_review` raises `LLMError`, the service catches it
    and persists `failed`. The Langfuse generation still fires (so
    operators can see the upload attempted an LLM call) but with the
    `parsed: False` summary marker."""
    langfuse, inner_trace, inner_gen = _mock_langfuse_client()
    service, _ = _deterministic_service(_RaisingProvider(), langfuse_client=langfuse)
    app.dependency_overrides[get_review_service] = lambda: service
    token = mint_token(
        user_id="u_adult",
        persona_type="intern",
        is_minor=False,
        age_set=True,
    )

    resp = await client.post(
        "/v1/review/uploads",
        headers={"Authorization": f"Bearer {token}"},
        json={"text": _SAMPLE_TEXT},
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "failed"

    # Trace opened, generation recorded with `parsed=False` so the
    # operator can see "yes the LLM was attempted, no it didn't yield
    # a parseable result". Trace finished with `status: failed`.
    inner_trace.generation.assert_called_once()  # type: ignore[attr-defined]
    _gen_args, _gen_kwargs = inner_trace.generation.call_args  # type: ignore[attr-defined]
    inner_gen.end.assert_called_once()  # type: ignore[attr-defined]
    end_kwargs = inner_gen.end.call_args.kwargs  # type: ignore[attr-defined]
    assert end_kwargs["output"]["parsed"] is False

    final_call = inner_trace.update.call_args_list[-1]  # type: ignore[attr-defined]
    assert final_call.kwargs["output"]["status"] == "failed"
