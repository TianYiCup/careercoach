"""HTTP-layer tests for the 复盘师 (review) routes — POST + GET.

A-8 pinned the gate ladder while the handler was a 501 stub. A-11
makes both routes real, so the tests now also exercise:

  * sync end-to-end POST (fake provider returns the canned reviewer
    contract; the route persists the result and returns `done`)
  * GET happy path returning the full record + summary
  * GET 404 for unknown ids and for cross-user reads (must not
    distinguish — silently 404 in both cases per the route's
    enumerate-resistant ownership pattern)
  * sync POST with an LLM that errors → status flips to `failed`
    rather than 5xx
  * gate ladder still fires before the service is touched (no token
    → 401, age unset → 403, body validation → 422)
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, datetime
from itertools import count

import pytest
from app.llm import Message
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
    ReviewService,
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
    ) -> AsyncIterator[str]:
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
    ) -> AsyncIterator[str]:
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


def _deterministic_service(
    provider: object,
    *,
    moderation: ModerationService | None = None,
) -> tuple[ReviewService, InMemoryReviewRepository]:
    """Build a service with deterministic id factory + clock so tests
    can assert on stable values without monkeypatching."""
    repo = InMemoryReviewRepository()
    counter = count(1)
    clock_counter = count(0)
    service = ReviewService(
        repo=repo,
        provider=provider,  # type: ignore[arg-type]  # _FakeProvider satisfies LLMProvider structurally
        moderation=moderation or _moderation(),
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
