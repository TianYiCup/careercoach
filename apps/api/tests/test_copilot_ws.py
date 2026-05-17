"""WebSocket audio bridge + moderation + Coach K hint tests for the
copilot stream.

A-16 shipped the WS scaffold with an echo loop; A-18 replaced the
echo with the audio bridge; A-19 chained moderation after each
`asr_final`; A-20 chains the Coach K hint after each non-blocked
moderation. This module pins the contract for all four.

What's locked here (do not regress)
-----------------------------------
    handshake               → mark_connected (status flips, connected_at populated)
    audio bytes             → DummyASR emits a partial per chunk
    audio_end frame         → DummyASR emits exactly one final
    asr_final w/ text       → moderation event follows
    verdict allow|warn      → background hint task (hint_delta+hint_done)
    verdict redirect|block  → no hint task spawns
    LLM error               → one hint_error event, WS stays open
    empty asr_final         → no moderation, no hint
    multi-utterance         → second audio_end starts the next utterance
    client disconnect       → mark_ended after draining hint tasks
    unknown copilot_id      → WS close 4404 (UNKNOWN_COPILOT)
    already-used id         → WS close 4409 (SESSION_ALREADY_USED)
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator, Iterator
from datetime import UTC, datetime
from itertools import count
from unittest.mock import MagicMock

import pytest
from app.asr import (
    ASRAuthError,
    ASRError,
    ASREvent,
    ASRTimeoutError,
    ASRUpstreamError,
    get_asr_provider,
)
from app.llm import LLMAuthError, Message, TokenUsage
from app.llm.factory import get_llm_router
from app.llm.provider import DEFAULT_TEMPERATURE, DEFAULT_TIMEOUT_SECONDS
from app.main import app
from app.observability.langfuse import get_langfuse_client
from app.schemas.moderation import RedirectResource
from app.services.copilot import (
    CopilotService,
    CopilotSessionRecord,
    InMemoryCopilotRepository,
    get_copilot_service,
)
from app.services.moderation import (
    DictBackend,
    LogOnlyEventSink,
    ModerationBackendError,
    ModerationService,
    NoopBackend,
    get_moderation_service,
)
from app.services.moderation.types import Decision
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

_WS_BASE_URL = "ws://test.local"
_SEED_CREATED_AT = datetime(2026, 5, 16, 11, 59, tzinfo=UTC)
_AUDIO_END_FRAME = json.dumps({"type": "audio_end"})

_ALLOW_MOD = {
    "type": "moderation",
    "verdict": "allow",
    "categories": [],
    "score": 0.0,
    "redirect_resource": None,
}


# --------------------------------------------------------------------- #
# Stub LLM router — controls hint behavior in tests                       #
# --------------------------------------------------------------------- #


class _StubLLMRouter:
    """Minimal LLMProvider impl for tests.

    Three modes:
      * `chunks=()` (default)   → emits no chunks; hint task converges
                                  on "no events" so audio-bridge tests
                                  don't have to assert on hint output
      * `chunks=("a", "b")`     → emits each as a delta; hint_done
                                  follows with the joined text
      * `raises=LLMAuthError(.)` → raises before yielding anything;
                                   handler emits a hint_error event
    """

    name = "stub"

    def __init__(
        self,
        *,
        chunks: tuple[str, ...] = (),
        raises: Exception | None = None,
    ) -> None:
        self._chunks = chunks
        self._raises = raises

    async def stream_chat(
        self,
        messages: list[Message],
        *,
        temperature: float = DEFAULT_TEMPERATURE,
        timeout: float = DEFAULT_TIMEOUT_SECONDS,
        usage_sink: list[TokenUsage] | None = None,
    ) -> AsyncIterator[str]:
        _ = (messages, temperature, timeout, usage_sink)
        if self._raises is not None:
            raise self._raises
            yield ""  # unreachable; keeps this an async generator
        for chunk in self._chunks:
            yield chunk


def _build_service() -> tuple[CopilotService, InMemoryCopilotRepository]:
    """Service with deterministic id + clock so tests can assert on
    stable timestamps without monkeypatching the module."""
    repo = InMemoryCopilotRepository()
    id_counter = count(1)
    clock_counter = count(0)
    service = CopilotService(
        repo=repo,
        ws_base_url=_WS_BASE_URL,
        id_factory=lambda: f"cop_test{next(id_counter):010d}",
        clock=lambda: datetime(2026, 5, 16, 12, next(clock_counter), tzinfo=UTC),
    )
    return service, repo


def _seed_pending(
    repo: InMemoryCopilotRepository,
    copilot_id: str,
    *,
    user_id: str = "u_demo",
    privacy_level: str = "standard",
) -> CopilotSessionRecord:
    record = CopilotSessionRecord(
        copilot_id=copilot_id,
        user_id=user_id,
        scenario_hint="interview salary negotiation",
        privacy_level=privacy_level,  # type: ignore[arg-type]
        status="pending",
        created_at=_SEED_CREATED_AT,
        connected_at=None,
        ended_at=None,
    )
    asyncio.run(repo.create(record))
    return record


def _install_moderation(*, with_dict: bool = False) -> None:
    """Install a non-DB moderation service for the WS tests.

    `with_dict=False` → `NoopBackend` (verdict=allow always).
    `with_dict=True`  → `DictBackend.from_file()` (real red-line keywords).
    Both wire `LogOnlyEventSink` so the audit row write doesn't
    require a postgres connection.
    """
    backend = DictBackend.from_file() if with_dict else NoopBackend()
    service = ModerationService(backend=backend, event_sink=LogOnlyEventSink())
    app.dependency_overrides[get_moderation_service] = lambda: service


def _install_llm(
    *,
    chunks: tuple[str, ...] = (),
    raises: Exception | None = None,
) -> _StubLLMRouter:
    """Install a stub LLMRouter. Defaults to no-chunks so audio-bridge
    tests don't see hint events."""
    stub = _StubLLMRouter(chunks=chunks, raises=raises)
    app.dependency_overrides[get_llm_router] = lambda: stub
    return stub


def _install_langfuse_mock() -> tuple[MagicMock, MagicMock]:
    """Install a MagicMock Langfuse client so trace integration tests
    can assert on the calls our code makes. Returns (client, trace)
    so tests can drill into both."""
    client = MagicMock(name="langfuse_client")
    trace = MagicMock(name="trace")
    trace.generation.return_value = MagicMock(name="generation")
    client.trace.return_value = trace
    app.dependency_overrides[get_langfuse_client] = lambda: client
    return client, trace


@pytest.fixture
def client() -> Iterator[TestClient]:
    """Sync TestClient — WS support is sync-only in starlette.

    Defaults all three external dependencies to deterministic stubs:
      * ASR: cleared lru_cache so the dummy-from-default-settings
             provider is fresh
      * Moderation: NoopBackend + LogOnlyEventSink (no DB, always
             returns verdict=allow)
      * LLM: empty-chunk stub so hint tasks emit no events
    Tests that care about a different stub override per-test via
    `_install_moderation(with_dict=True)` or `_install_llm(chunks=...)`.
    """
    get_asr_provider.cache_clear()
    _install_moderation(with_dict=False)
    _install_llm()
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()
    get_asr_provider.cache_clear()


# --------------------------------------------------------------------- #
# Audio-bridge happy paths                                               #
# --------------------------------------------------------------------- #


def test_handshake_flips_status_to_connected(client: TestClient) -> None:
    service, repo = _build_service()
    app.dependency_overrides[get_copilot_service] = lambda: service
    _seed_pending(repo, "cop_test0000000001")

    with client.websocket_connect("/v1/copilot/sessions/cop_test0000000001/stream"):
        mid = asyncio.run(repo.get("cop_test0000000001"))
        assert mid is not None
        assert mid.status == "connected"
        assert mid.connected_at is not None
        assert mid.ended_at is None


def test_single_utterance_emits_partial_final_then_moderation(client: TestClient) -> None:
    """Audio chunks → cumulative partials → exactly one final → one
    moderation event. With the empty-chunk LLM stub, no hint events
    follow."""
    service, repo = _build_service()
    app.dependency_overrides[get_copilot_service] = lambda: service
    _seed_pending(repo, "cop_test0000000001")

    with client.websocket_connect("/v1/copilot/sessions/cop_test0000000001/stream") as ws:
        ws.send_bytes(b"hello ")
        ws.send_bytes(b"world")
        ws.send_text(_AUDIO_END_FRAME)
        events = [ws.receive_json() for _ in range(4)]

    assert events == [
        {"type": "asr_partial", "text": "hello "},
        {"type": "asr_partial", "text": "hello world"},
        {"type": "asr_final", "text": "hello world"},
        _ALLOW_MOD,
    ]


def test_single_chunk_utterance(client: TestClient) -> None:
    service, repo = _build_service()
    app.dependency_overrides[get_copilot_service] = lambda: service
    _seed_pending(repo, "cop_test0000000001")

    with client.websocket_connect("/v1/copilot/sessions/cop_test0000000001/stream") as ws:
        ws.send_bytes(b"hi")
        ws.send_text(_AUDIO_END_FRAME)
        events = [ws.receive_json() for _ in range(3)]

    assert events == [
        {"type": "asr_partial", "text": "hi"},
        {"type": "asr_final", "text": "hi"},
        _ALLOW_MOD,
    ]


def test_empty_utterance_emits_only_final_no_moderation(client: TestClient) -> None:
    """Empty `audio_end` → asr_final w/ empty text + NO moderation
    event + NO hint event."""
    service, repo = _build_service()
    app.dependency_overrides[get_copilot_service] = lambda: service
    _seed_pending(repo, "cop_test0000000001")

    with client.websocket_connect("/v1/copilot/sessions/cop_test0000000001/stream") as ws:
        ws.send_text(_AUDIO_END_FRAME)
        first_final = ws.receive_json()
        ws.send_bytes(b"after")
        ws.send_text(_AUDIO_END_FRAME)
        next_partial = ws.receive_json()

    assert first_final == {"type": "asr_final", "text": ""}
    assert next_partial == {"type": "asr_partial", "text": "after"}


def test_multi_utterance_in_one_connection(client: TestClient) -> None:
    """Two utterances in one connection. Each gets its own moderation
    event; with the empty-chunk LLM stub, no hint events follow."""
    service, repo = _build_service()
    app.dependency_overrides[get_copilot_service] = lambda: service
    _seed_pending(repo, "cop_test0000000001")

    with client.websocket_connect("/v1/copilot/sessions/cop_test0000000001/stream") as ws:
        ws.send_bytes(b"first")
        ws.send_text(_AUDIO_END_FRAME)
        first = [ws.receive_json() for _ in range(3)]

        ws.send_bytes(b"second")
        ws.send_text(_AUDIO_END_FRAME)
        second = [ws.receive_json() for _ in range(3)]

    assert first == [
        {"type": "asr_partial", "text": "first"},
        {"type": "asr_final", "text": "first"},
        _ALLOW_MOD,
    ]
    assert second == [
        {"type": "asr_partial", "text": "second"},
        {"type": "asr_final", "text": "second"},
        _ALLOW_MOD,
    ]


def test_unknown_text_frames_are_ignored(client: TestClient) -> None:
    service, repo = _build_service()
    app.dependency_overrides[get_copilot_service] = lambda: service
    _seed_pending(repo, "cop_test0000000001")

    with client.websocket_connect("/v1/copilot/sessions/cop_test0000000001/stream") as ws:
        ws.send_text("not even json")
        ws.send_text(json.dumps({"type": "something_else"}))
        ws.send_bytes(b"ok")
        ws.send_text(_AUDIO_END_FRAME)
        events = [ws.receive_json() for _ in range(3)]

    assert events == [
        {"type": "asr_partial", "text": "ok"},
        {"type": "asr_final", "text": "ok"},
        _ALLOW_MOD,
    ]


def test_client_disconnect_flips_status_to_ended(client: TestClient) -> None:
    service, repo = _build_service()
    app.dependency_overrides[get_copilot_service] = lambda: service
    _seed_pending(repo, "cop_test0000000001")

    with client.websocket_connect("/v1/copilot/sessions/cop_test0000000001/stream") as ws:
        ws.send_bytes(b"noop")
        ws.send_text(_AUDIO_END_FRAME)
        for _ in range(3):
            ws.receive_json()

    final = asyncio.run(repo.get("cop_test0000000001"))
    assert final is not None
    assert final.status == "ended"
    assert final.connected_at is not None
    assert final.ended_at is not None
    assert final.ended_at > final.connected_at


def test_disconnect_mid_utterance_still_flips_ended(client: TestClient) -> None:
    service, repo = _build_service()
    app.dependency_overrides[get_copilot_service] = lambda: service
    _seed_pending(repo, "cop_test0000000001")

    with client.websocket_connect("/v1/copilot/sessions/cop_test0000000001/stream") as ws:
        ws.send_bytes(b"partial")

    final = asyncio.run(repo.get("cop_test0000000001"))
    assert final is not None
    assert final.status == "ended"


# --------------------------------------------------------------------- #
# Moderation contract                                                    #
# --------------------------------------------------------------------- #


def test_self_harm_text_emits_redirect_with_resource(client: TestClient) -> None:
    """A finalized transcript containing a `self_harm` keyword must
    emit a moderation event with `verdict=redirect` and a
    `redirect_resource` carrying the crisis-line. Compliance-load-bearing
    (PRD §3.0.5 A) — minor or adult, the help-line must surface."""
    _install_moderation(with_dict=True)
    service, repo = _build_service()
    app.dependency_overrides[get_copilot_service] = lambda: service
    _seed_pending(repo, "cop_test0000000001")

    with client.websocket_connect("/v1/copilot/sessions/cop_test0000000001/stream") as ws:
        ws.send_bytes("我想死".encode())
        ws.send_text(_AUDIO_END_FRAME)
        events = [ws.receive_json() for _ in range(3)]

    moderation = events[2]
    assert moderation["type"] == "moderation"
    assert moderation["verdict"] == "redirect"
    assert moderation["categories"] == ["self_harm"]
    assert moderation["score"] > 0.5
    assert moderation["redirect_resource"] is not None
    assert moderation["redirect_resource"]["title"]
    assert moderation["redirect_resource"]["url"]


def test_red_line_text_emits_block_verdict(client: TestClient) -> None:
    _install_moderation(with_dict=True)
    service, repo = _build_service()
    app.dependency_overrides[get_copilot_service] = lambda: service
    _seed_pending(repo, "cop_test0000000001")

    with client.websocket_connect("/v1/copilot/sessions/cop_test0000000001/stream") as ws:
        ws.send_bytes("校园暴力".encode())
        ws.send_text(_AUDIO_END_FRAME)
        events = [ws.receive_json() for _ in range(3)]

    moderation = events[2]
    assert moderation["type"] == "moderation"
    assert moderation["verdict"] == "block"
    assert "violence" in moderation["categories"]
    assert moderation["redirect_resource"] is None


def test_clean_text_emits_allow_with_local_dict(client: TestClient) -> None:
    _install_moderation(with_dict=True)
    service, repo = _build_service()
    app.dependency_overrides[get_copilot_service] = lambda: service
    _seed_pending(repo, "cop_test0000000001")

    with client.websocket_connect("/v1/copilot/sessions/cop_test0000000001/stream") as ws:
        ws.send_bytes(b"interview salary negotiation")
        ws.send_text(_AUDIO_END_FRAME)
        events = [ws.receive_json() for _ in range(3)]

    moderation = events[2]
    assert moderation["type"] == "moderation"
    assert moderation["verdict"] == "allow"
    assert moderation["categories"] == []


# --------------------------------------------------------------------- #
# Coach K hint contract                                                  #
# --------------------------------------------------------------------- #


def test_allow_verdict_streams_hint_deltas_then_done(client: TestClient) -> None:
    """When moderation says `allow`, the LLM router is invoked and its
    chunks are forwarded as `hint_delta` events, terminated by a
    `hint_done` event with the joined transcript."""
    _install_llm(chunks=("先回应", "对方观点，", "再说自己的需求。"))
    service, repo = _build_service()
    app.dependency_overrides[get_copilot_service] = lambda: service
    _seed_pending(repo, "cop_test0000000001")

    with client.websocket_connect("/v1/copilot/sessions/cop_test0000000001/stream") as ws:
        ws.send_bytes(b"the other side just spoke")
        ws.send_text(_AUDIO_END_FRAME)
        # asr_partial, asr_final, moderation, hint_delta x3, hint_done = 7
        events = [ws.receive_json() for _ in range(7)]

    assert events[3] == {"type": "hint_delta", "text": "先回应"}
    assert events[4] == {"type": "hint_delta", "text": "对方观点，"}
    assert events[5] == {"type": "hint_delta", "text": "再说自己的需求。"}
    assert events[6] == {
        "type": "hint_done",
        "text": "先回应对方观点，再说自己的需求。",
    }


def test_block_verdict_skips_hint(client: TestClient) -> None:
    """When moderation blocks, the LLM is never invoked. The stub
    would yield chunks, but the gate keeps it from being asked."""
    _install_moderation(with_dict=True)
    _install_llm(chunks=("would never reach here",))
    service, repo = _build_service()
    app.dependency_overrides[get_copilot_service] = lambda: service
    _seed_pending(repo, "cop_test0000000001")

    with client.websocket_connect("/v1/copilot/sessions/cop_test0000000001/stream") as ws:
        ws.send_bytes("校园暴力".encode())
        ws.send_text(_AUDIO_END_FRAME)
        # Only 3 events: partial, final, moderation. No hint follows.
        first_three = [ws.receive_json() for _ in range(3)]
        # Send another utterance so we can observe the next event is
        # the new partial — proves no hint was spawned for the prior.
        ws.send_bytes(b"different")
        ws.send_text(_AUDIO_END_FRAME)
        next_event = ws.receive_json()

    assert first_three[2]["type"] == "moderation"
    assert first_three[2]["verdict"] == "block"
    assert next_event == {"type": "asr_partial", "text": "different"}


def test_redirect_verdict_skips_hint(client: TestClient) -> None:
    """Same as block: redirect (self_harm) suppresses the LLM call.
    The redirect_resource on the moderation event is what the client
    surfaces; there's nothing for K to coach toward."""
    _install_moderation(with_dict=True)
    _install_llm(chunks=("never",))
    service, repo = _build_service()
    app.dependency_overrides[get_copilot_service] = lambda: service
    _seed_pending(repo, "cop_test0000000001")

    with client.websocket_connect("/v1/copilot/sessions/cop_test0000000001/stream") as ws:
        ws.send_bytes("我想死".encode())
        ws.send_text(_AUDIO_END_FRAME)
        first_three = [ws.receive_json() for _ in range(3)]
        ws.send_bytes(b"different")
        ws.send_text(_AUDIO_END_FRAME)
        next_event = ws.receive_json()

    assert first_three[2]["verdict"] == "redirect"
    assert next_event == {"type": "asr_partial", "text": "different"}


def test_llm_failure_emits_hint_error(client: TestClient) -> None:
    """If the LLM raises an `LLMError`, the WS emits one
    `hint_error` event so the client knows the hint is unavailable.
    The session stays open."""
    _install_llm(raises=LLMAuthError("no creds", provider="stub"))
    service, repo = _build_service()
    app.dependency_overrides[get_copilot_service] = lambda: service
    _seed_pending(repo, "cop_test0000000001")

    with client.websocket_connect("/v1/copilot/sessions/cop_test0000000001/stream") as ws:
        ws.send_bytes(b"speak to the coach")
        ws.send_text(_AUDIO_END_FRAME)
        # asr_partial, asr_final, moderation, hint_error = 4
        events = [ws.receive_json() for _ in range(4)]
        # Session still alive — send another utterance.
        ws.send_bytes(b"again")
        ws.send_text(_AUDIO_END_FRAME)
        next_partial = ws.receive_json()

    assert events[3] == {"type": "hint_error", "message": "hint unavailable"}
    assert next_partial == {"type": "asr_partial", "text": "again"}


async def test_cancel_in_flight_hints_cancels_pending_and_swallows_errors() -> None:
    """A-22: `_cancel_in_flight_hints` cancels every not-yet-done
    task in the set and awaits them all (swallowing CancelledError /
    any other late-arriving exception). Direct unit test of the
    helper so we don't have to thread cancellation timing through
    the WS TestClient portal."""
    from app.routes.v1.copilot import _cancel_in_flight_hints

    cancelled_seen: list[bool] = []

    async def slow_task() -> None:
        try:
            await asyncio.sleep(60)
        except asyncio.CancelledError:
            cancelled_seen.append(True)
            raise

    async def fast_task() -> None:
        # Already done by the time we call cancel — must be skipped
        # from the pending list.
        return

    tasks: set[asyncio.Task[None]] = {asyncio.create_task(slow_task()) for _ in range(3)}
    done_task = asyncio.create_task(fast_task())
    await done_task
    tasks.add(done_task)

    await _cancel_in_flight_hints(tasks, copilot_id="cop_unit")

    # All three slow tasks observed CancelledError. The fast (already
    # done) task contributed nothing.
    assert cancelled_seen == [True, True, True]
    # No exception leaked despite the CancelledError storm.


async def test_cancel_in_flight_hints_no_op_on_empty_set() -> None:
    """The set may be empty if no hints were ever spawned (every
    utterance was empty / blocked). Helper must short-circuit cleanly."""
    from app.routes.v1.copilot import _cancel_in_flight_hints

    await _cancel_in_flight_hints(set(), copilot_id="cop_unit")


def test_multi_utterance_hint_does_not_leak_into_next_utterance(
    client: TestClient,
) -> None:
    """End-to-end sanity that the cancel-between-utterances behavior
    keeps utterance N+1's event stream clean. Uses the default
    no-chunks LLM stub so we exercise the multi-utterance flow
    without long-running tasks confusing the TestClient portal — the
    cancel helper is unit-tested above for the actual cancellation
    semantics."""
    service, repo = _build_service()
    app.dependency_overrides[get_copilot_service] = lambda: service
    _seed_pending(repo, "cop_test0000000001")

    with client.websocket_connect("/v1/copilot/sessions/cop_test0000000001/stream") as ws:
        ws.send_bytes(b"first")
        ws.send_text(_AUDIO_END_FRAME)
        u1 = [ws.receive_json() for _ in range(3)]
        ws.send_bytes(b"second")
        ws.send_text(_AUDIO_END_FRAME)
        u2 = [ws.receive_json() for _ in range(3)]

    # No hint events leaked between utterances. Each utterance's
    # event stream is partial → final → moderation, full stop.
    assert [e["type"] for e in u1] == ["asr_partial", "asr_final", "moderation"]
    assert [e["type"] for e in u2] == ["asr_partial", "asr_final", "moderation"]


def test_empty_llm_stream_emits_no_hint_events(client: TestClient) -> None:
    """Default fixture stub yields no chunks → no hint events at all
    (not even an empty hint_done). Pins the "skip empty hints" rule
    so the existing audio-bridge tests above don't accidentally start
    seeing hint events."""
    service, repo = _build_service()
    app.dependency_overrides[get_copilot_service] = lambda: service
    _seed_pending(repo, "cop_test0000000001")

    with client.websocket_connect("/v1/copilot/sessions/cop_test0000000001/stream") as ws:
        ws.send_bytes(b"hello")
        ws.send_text(_AUDIO_END_FRAME)
        # Receive the 3 expected events.
        for _ in range(3):
            ws.receive_json()
        # Send another utterance and confirm the next event is a
        # partial — nothing leaked from the prior empty hint run.
        ws.send_bytes(b"again")
        ws.send_text(_AUDIO_END_FRAME)
        next_event = ws.receive_json()

    assert next_event == {"type": "asr_partial", "text": "again"}


# --------------------------------------------------------------------- #
# Langfuse trace integration                                             #
# --------------------------------------------------------------------- #


def _generation_call_names(trace: MagicMock) -> list[str]:
    """Helper — extract the `name=` kwarg from each `trace.generation`
    invocation so tests can assert on the ordered list of generation
    names without coupling to the rest of the kwargs."""
    return [call.kwargs["name"] for call in trace.generation.call_args_list]


def test_default_no_langfuse_client_is_noop(client: TestClient) -> None:
    """When `get_langfuse_client()` returns None (no LANGFUSE_*
    env vars), the WS handler still runs the full pipeline — the
    trace wrapper short-circuits every method internally. This test
    is the safety net for dev-without-Langfuse runs."""
    # Default fixture leaves get_langfuse_client unmocked → real call
    # returns None because no env var was set.
    service, repo = _build_service()
    app.dependency_overrides[get_copilot_service] = lambda: service
    _seed_pending(repo, "cop_test0000000001")

    with client.websocket_connect("/v1/copilot/sessions/cop_test0000000001/stream") as ws:
        ws.send_bytes(b"hi")
        ws.send_text(_AUDIO_END_FRAME)
        events = [ws.receive_json() for _ in range(3)]

    # Pipeline produced the expected events; no exception bubbled from
    # the no-op trace calls.
    assert [e["type"] for e in events] == ["asr_partial", "asr_final", "moderation"]


def test_utterance_creates_named_trace_with_session_metadata(client: TestClient) -> None:
    """One trace per utterance, named `copilot_utterance`. `copilot_id`
    lifts to the Langfuse top-level `session_id` field (A-23) so every
    utterance in one WS connection lands under the same session row;
    `user_id` stays in metadata for cross-session filtering."""
    lf_client, _trace = _install_langfuse_mock()
    service, repo = _build_service()
    app.dependency_overrides[get_copilot_service] = lambda: service
    _seed_pending(repo, "cop_test0000000001")

    with client.websocket_connect("/v1/copilot/sessions/cop_test0000000001/stream") as ws:
        ws.send_bytes(b"hi")
        ws.send_text(_AUDIO_END_FRAME)
        for _ in range(3):
            ws.receive_json()

    lf_client.trace.assert_called_once()
    _, kwargs = lf_client.trace.call_args
    assert kwargs["name"] == "copilot_utterance"
    assert kwargs["session_id"] == "cop_test0000000001"
    assert kwargs["metadata"]["user_id"] == "u_demo"
    assert "copilot_id" not in kwargs["metadata"]
    assert kwargs["input"]["scenario_hint"] == "interview salary negotiation"


def test_utterance_records_transcribe_and_moderate_generations(
    client: TestClient,
) -> None:
    """The trace gets a `transcribe` generation (model=ASR provider)
    plus a `moderate` generation (model=moderation_pipeline). Hint
    isn't expected here — default LLM stub yields no chunks.

    `output` ends up on the chained `.end()` call, not the
    `generation()` constructor (see TurnTrace.record_generation), so
    we look at both call sites to assert end-to-end content.
    """
    _lf_client, trace = _install_langfuse_mock()
    service, repo = _build_service()
    app.dependency_overrides[get_copilot_service] = lambda: service
    _seed_pending(repo, "cop_test0000000001")

    with client.websocket_connect("/v1/copilot/sessions/cop_test0000000001/stream") as ws:
        ws.send_bytes(b"hi")
        ws.send_text(_AUDIO_END_FRAME)
        for _ in range(3):
            ws.receive_json()

    assert _generation_call_names(trace) == ["transcribe", "moderate"]
    transcribe_call = trace.generation.call_args_list[0]
    assert transcribe_call.kwargs["model"] == "dummy"
    # `.end()` collects the output payload after `.generation()` returns.
    end_calls = trace.generation.return_value.end.call_args_list
    assert end_calls[0].kwargs["output"] == {"text": "hi", "char_count": 2}


def test_hint_records_coach_hint_generation_after_text(
    client: TestClient,
) -> None:
    """When the LLM router yields chunks, a `coach_hint` generation
    is added to the trace with the joined text. The order is
    transcribe → moderate → coach_hint (hint runs in the background
    but completes before the WS context exits because we drain on
    finally)."""
    _install_llm(chunks=("先回应", "对方观点。"))
    _lf_client, trace = _install_langfuse_mock()
    service, repo = _build_service()
    app.dependency_overrides[get_copilot_service] = lambda: service
    _seed_pending(repo, "cop_test0000000001")

    with client.websocket_connect("/v1/copilot/sessions/cop_test0000000001/stream") as ws:
        ws.send_bytes(b"hi")
        ws.send_text(_AUDIO_END_FRAME)
        # 5 events: partial, final, moderation, hint_delta x2, hint_done
        for _ in range(6):
            ws.receive_json()

    names = _generation_call_names(trace)
    assert "coach_hint" in names
    coach_index = names.index("coach_hint")
    coach_call = trace.generation.call_args_list[coach_index]
    assert coach_call.kwargs["model"] == "stub"
    # `.end()` calls are recorded in the same order as `.generation()`
    # calls (each generation immediately ends in `record_generation`).
    end_calls = trace.generation.return_value.end.call_args_list
    assert end_calls[coach_index].kwargs["output"] == {"text": "先回应对方观点。"}


def test_blocked_verdict_skips_coach_hint_generation(client: TestClient) -> None:
    """When moderation blocks, no LLM call → no `coach_hint`
    generation. The trace still has transcribe + moderate."""
    _install_moderation(with_dict=True)
    _install_llm(chunks=("never reached",))
    _lf_client, trace = _install_langfuse_mock()
    service, repo = _build_service()
    app.dependency_overrides[get_copilot_service] = lambda: service
    _seed_pending(repo, "cop_test0000000001")

    with client.websocket_connect("/v1/copilot/sessions/cop_test0000000001/stream") as ws:
        ws.send_bytes("校园暴力".encode())
        ws.send_text(_AUDIO_END_FRAME)
        for _ in range(3):
            ws.receive_json()

    assert _generation_call_names(trace) == ["transcribe", "moderate"]


def test_empty_utterance_records_only_transcribe(client: TestClient) -> None:
    """Empty `audio_end` → transcribe generation runs (with empty
    text) but moderate + coach_hint are skipped. The trace still
    finishes with output={final_text:'', verdict:None} so analysts
    see the empty utterance on the timeline."""
    _lf_client, trace = _install_langfuse_mock()
    service, repo = _build_service()
    app.dependency_overrides[get_copilot_service] = lambda: service
    _seed_pending(repo, "cop_test0000000001")

    with client.websocket_connect("/v1/copilot/sessions/cop_test0000000001/stream") as ws:
        ws.send_text(_AUDIO_END_FRAME)
        ws.receive_json()  # asr_final empty

    assert _generation_call_names(trace) == ["transcribe"]
    # Trace output reflects the empty outcome.
    trace.update.assert_any_call(output={"final_text": "", "verdict": None})


def test_multi_utterance_creates_one_trace_per_utterance(client: TestClient) -> None:
    """Per-utterance traces, not per-connection. Two utterances → two
    `client.trace(...)` calls."""
    lf_client, _trace = _install_langfuse_mock()
    service, repo = _build_service()
    app.dependency_overrides[get_copilot_service] = lambda: service
    _seed_pending(repo, "cop_test0000000001")

    with client.websocket_connect("/v1/copilot/sessions/cop_test0000000001/stream") as ws:
        ws.send_bytes(b"first")
        ws.send_text(_AUDIO_END_FRAME)
        for _ in range(3):
            ws.receive_json()
        ws.send_bytes(b"second")
        ws.send_text(_AUDIO_END_FRAME)
        for _ in range(3):
            ws.receive_json()

    assert lf_client.trace.call_count == 2


def test_initial_tags_include_surface_and_privacy(client: TestClient) -> None:
    """A-24: trace creation gets `surface:copilot` + `privacy:standard`
    as initial tags so analysts can filter the Langfuse session UI
    without parsing trace names."""
    lf_client, _trace = _install_langfuse_mock()
    service, repo = _build_service()
    app.dependency_overrides[get_copilot_service] = lambda: service
    _seed_pending(repo, "cop_test0000000001")  # default privacy=standard

    with client.websocket_connect("/v1/copilot/sessions/cop_test0000000001/stream") as ws:
        ws.send_bytes(b"hi")
        ws.send_text(_AUDIO_END_FRAME)
        for _ in range(3):
            ws.receive_json()

    _, kwargs = lf_client.trace.call_args
    assert kwargs["tags"] == ["surface:copilot", "privacy:standard"]


def test_initial_tags_reflect_high_privacy_level(client: TestClient) -> None:
    """A-24: `privacy:high` rides through from the persisted record.
    Important because the future on-device-ASR path (US-B3) flips
    behavior on this — analysts need to spot it on the trace UI."""
    lf_client, _trace = _install_langfuse_mock()
    service, repo = _build_service()
    app.dependency_overrides[get_copilot_service] = lambda: service
    _seed_pending(repo, "cop_test0000000001", privacy_level="high")

    with client.websocket_connect("/v1/copilot/sessions/cop_test0000000001/stream") as ws:
        ws.send_bytes(b"hi")
        ws.send_text(_AUDIO_END_FRAME)
        for _ in range(3):
            ws.receive_json()

    _, kwargs = lf_client.trace.call_args
    assert kwargs["tags"] == ["surface:copilot", "privacy:high"]


def test_verdict_tag_added_after_moderation_runs(client: TestClient) -> None:
    """A-24/A-32: once user-input moderation produces a verdict,
    `verdict_input:xxx` is appended to the trace's tag list via
    `add_tags`. Lets analysts filter for all `verdict_input:redirect`
    (crisis-line escalations) etc. without joining against the DB.

    A-32 renamed the key from `verdict:` to `verdict_input:` to align
    with sandbox + review and make room for the new `verdict_output:`
    on the hint moderation."""
    _install_moderation(with_dict=True)
    _lf_client, trace = _install_langfuse_mock()
    service, repo = _build_service()
    app.dependency_overrides[get_copilot_service] = lambda: service
    _seed_pending(repo, "cop_test0000000001")

    with client.websocket_connect("/v1/copilot/sessions/cop_test0000000001/stream") as ws:
        ws.send_bytes("我想死".encode())  # self_harm → verdict_input:redirect
        ws.send_text(_AUDIO_END_FRAME)
        for _ in range(3):
            ws.receive_json()

    # The trace's update was called multiple times: A-31's asr_status
    # tag goes on FIRST (right after ASR returns), then A-24/A-32's
    # verdict_input tag goes on after moderation. The last `tags=`
    # call holds the cumulative union — that's what Langfuse renders.
    # Note: redirect verdict gates hint generation, so no
    # `verdict_output:` tag follows here.
    tag_calls = [c for c in trace.update.call_args_list if "tags" in c.kwargs]
    assert tag_calls, "expected at least one tags= update for the verdict tag"
    final_tag_call = tag_calls[-1]
    assert final_tag_call.kwargs["tags"] == [
        "surface:copilot",
        "privacy:standard",
        "asr_status:ok",
        "verdict_input:redirect",
    ]


def test_no_verdict_tag_when_utterance_is_empty(client: TestClient) -> None:
    """Empty utterance skips moderation entirely; therefore no
    `verdict:xxx` tag is added. Initial tags + `asr_status:ok` (A-31
    fires on every utterance, even empty — it reflects the ASR call,
    not the transcript content) is the full set."""
    lf_client, trace = _install_langfuse_mock()
    service, repo = _build_service()
    app.dependency_overrides[get_copilot_service] = lambda: service
    _seed_pending(repo, "cop_test0000000001")

    with client.websocket_connect("/v1/copilot/sessions/cop_test0000000001/stream") as ws:
        ws.send_text(_AUDIO_END_FRAME)
        ws.receive_json()  # asr_final empty

    # Trace was created with the initial tags...
    _, kwargs = lf_client.trace.call_args
    assert kwargs["tags"] == ["surface:copilot", "privacy:standard"]
    # ...and the only tags= update was the A-31 asr_status:ok extension.
    tag_calls = [c for c in trace.update.call_args_list if "tags" in c.kwargs]
    assert len(tag_calls) == 1
    assert tag_calls[0].kwargs["tags"] == [
        "surface:copilot",
        "privacy:standard",
        "asr_status:ok",
    ]


# --------------------------------------------------------------------- #
# Error paths — close-code contract                                      #
# --------------------------------------------------------------------- #


def test_unknown_copilot_id_closes_with_4404(client: TestClient) -> None:
    service, _ = _build_service()
    app.dependency_overrides[get_copilot_service] = lambda: service

    with pytest.raises(WebSocketDisconnect) as exc_info:  # noqa: SIM117
        with client.websocket_connect(
            "/v1/copilot/sessions/cop_does_not_exist/stream",
        ) as ws:
            ws.receive_text()

    assert exc_info.value.code == 4404


def test_already_ended_session_closes_with_4409(client: TestClient) -> None:
    service, repo = _build_service()
    app.dependency_overrides[get_copilot_service] = lambda: service
    _seed_pending(repo, "cop_test0000000001")

    with client.websocket_connect("/v1/copilot/sessions/cop_test0000000001/stream"):
        pass

    with pytest.raises(WebSocketDisconnect) as exc_info:  # noqa: SIM117
        with client.websocket_connect(
            "/v1/copilot/sessions/cop_test0000000001/stream",
        ) as ws:
            ws.receive_text()

    assert exc_info.value.code == 4409


def test_unknown_copilot_id_does_not_mint_a_row(client: TestClient) -> None:
    service, repo = _build_service()
    app.dependency_overrides[get_copilot_service] = lambda: service

    with pytest.raises(WebSocketDisconnect):  # noqa: SIM117
        with client.websocket_connect(
            "/v1/copilot/sessions/cop_phantom/stream",
        ) as ws:
            ws.receive_text()

    assert asyncio.run(repo.get("cop_phantom")) is None


# --------------------------------------------------------------------- #
# A-30: ASR error handling on the WS bridge                             #
# --------------------------------------------------------------------- #


class _FailingASRProvider:
    """ASRProvider stub that drains the audio queue then raises a
    scripted `ASRError`. Modeled on a mid-utterance failure (token
    expired, network blip) where the user already finished talking
    before the error surfaced — the most common production shape."""

    name = "failing_asr"

    def __init__(self, error: ASRError) -> None:
        self._error = error

    async def transcribe_stream(
        self,
        audio_chunks: AsyncIterator[bytes],
        *,
        timeout: float = 8.0,
    ) -> AsyncIterator[ASREvent]:
        _ = timeout
        async for _chunk in audio_chunks:
            pass
        raise self._error
        yield ASREvent(kind="final", text="")  # pragma: no cover — unreachable


def _install_asr(provider: object) -> None:
    """Override the ASR DI to a caller-supplied stub. Tests must call
    `get_asr_provider.cache_clear()` themselves AFTER returning so
    the lru_cache doesn't leak a stale provider."""
    app.dependency_overrides[get_asr_provider] = lambda: provider


def test_asr_auth_error_emits_asr_error_event_and_skips_moderation(
    client: TestClient,
) -> None:
    """When the ASR provider raises ASRAuthError mid-utterance the
    bridge MUST emit a single `asr_error` event with an opaque
    message and SKIP downstream moderation + hint — a transient
    vendor outage shouldn't tear down a live coaching session."""
    service, repo = _build_service()
    app.dependency_overrides[get_copilot_service] = lambda: service
    _seed_pending(repo, "cop_test0000000001")
    _install_asr(_FailingASRProvider(ASRAuthError("token revoked", provider="aliyun")))

    with client.websocket_connect("/v1/copilot/sessions/cop_test0000000001/stream") as ws:
        ws.send_bytes(b"audio")
        ws.send_text(_AUDIO_END_FRAME)
        first = ws.receive_json()
        # Send a second utterance to prove the connection stayed open
        # and the next utterance routes through normally (also using
        # our failing provider — so it errors again, deterministically).
        ws.send_bytes(b"more")
        ws.send_text(_AUDIO_END_FRAME)
        second = ws.receive_json()

    assert first == {"type": "asr_error", "message": "transcription unavailable"}
    assert second == {"type": "asr_error", "message": "transcription unavailable"}


def test_asr_upstream_error_does_not_emit_partial_or_final(
    client: TestClient,
) -> None:
    """`ASRUpstreamError` from a vendor 5xx — same client-facing
    treatment as auth errors (opaque `asr_error`, no
    `asr_partial`/`asr_final` for the failed utterance)."""
    service, repo = _build_service()
    app.dependency_overrides[get_copilot_service] = lambda: service
    _seed_pending(repo, "cop_test0000000001")
    _install_asr(
        _FailingASRProvider(ASRUpstreamError("upstream 502", provider="aliyun", status_code=502))
    )

    with client.websocket_connect("/v1/copilot/sessions/cop_test0000000001/stream") as ws:
        ws.send_bytes(b"audio")
        ws.send_text(_AUDIO_END_FRAME)
        events = [ws.receive_json()]

    # Exactly one event, and it's the error envelope. No moderation
    # event follows (because final_text is empty after the catch).
    assert events == [{"type": "asr_error", "message": "transcription unavailable"}]


def test_asr_error_does_not_trigger_hint_task(client: TestClient) -> None:
    """Hint generation is gated on (final_text non-empty + verdict
    allowed). An ASR error returns empty final_text, so the bridge
    MUST NOT spawn a hint task — even if the LLM stub were configured
    to emit chunks. We prove absence by sending a second utterance
    and asserting its response arrives immediately as `asr_error`,
    not preceded by hint_delta/hint_done from the first utterance."""
    service, repo = _build_service()
    app.dependency_overrides[get_copilot_service] = lambda: service
    _seed_pending(repo, "cop_test0000000001")
    _install_asr(_FailingASRProvider(ASRUpstreamError("scripted", provider="aliyun")))
    # Wire a chunky LLM stub. If a hint task spawned for the first
    # utterance, its hint_delta events would interleave between the
    # two `asr_error` events below — the assertion at the end pins
    # the absence of that leak.
    _install_llm(chunks=("would not appear", " in events"))

    with client.websocket_connect("/v1/copilot/sessions/cop_test0000000001/stream") as ws:
        ws.send_bytes(b"x")
        ws.send_text(_AUDIO_END_FRAME)
        first = ws.receive_json()
        ws.send_bytes(b"y")
        ws.send_text(_AUDIO_END_FRAME)
        second = ws.receive_json()

    assert first == {"type": "asr_error", "message": "transcription unavailable"}
    assert second == {"type": "asr_error", "message": "transcription unavailable"}


# --------------------------------------------------------------------- #
# A-30: AliyunASRProvider end-to-end integration through the WS bridge  #
# --------------------------------------------------------------------- #


class _FakeAliyunWSChannel:
    """Tiny duplicate of A-28's _FakeWSChannel — kept local to this
    test file to avoid a cross-test import. Same contract: scripted
    `to_send` for `recv()`, `sent` log for outbound writes, yields
    to the loop every `recv` so the feeder/consumer interleave."""

    def __init__(self, to_send: list[str | bytes]) -> None:
        self.sent: list[str | bytes] = []
        self._to_send = list(to_send)

    async def send(self, message: bytes | str) -> None:
        self.sent.append(message)

    async def recv(self) -> bytes | str:
        await asyncio.sleep(0)
        from websockets.exceptions import ConnectionClosed

        if not self._to_send:
            raise ConnectionClosed(rcvd=None, sent=None)
        return self._to_send.pop(0)

    async def close(self, code: int = 1000, reason: str = "") -> None:
        del code, reason


class _FakeAliyunWSConnection:
    def __init__(self, channel: _FakeAliyunWSChannel) -> None:
        self._channel = channel

    async def __aenter__(self) -> _FakeAliyunWSChannel:
        return self._channel

    async def __aexit__(self, *exc: object) -> None:
        return None


def _aliyun_provider_with_scripted_events(
    *,
    partials: tuple[str, ...],
    final: str,
) -> object:
    """Build an `AliyunASRProvider` whose token cache is pre-seeded
    and whose WS factory yields a fake channel with the supplied
    scripted events. The provider is otherwise unmodified so we're
    exercising the real adapter logic + the real bridge."""
    from app.asr import AliyunASRProvider
    from app.asr._aliyun_token import AccessToken, AliyunTokenCache

    events_json: list[str | bytes] = []
    for text in partials:
        events_json.append(
            json.dumps(
                {
                    "header": {"name": "TranscriptionResultChanged"},
                    "payload": {"result": text, "confidence": 0.9},
                }
            )
        )
    events_json.append(
        json.dumps(
            {
                "header": {"name": "SentenceEnd"},
                "payload": {"result": final, "confidence": 0.95},
            }
        )
    )

    channel = _FakeAliyunWSChannel(to_send=events_json)

    async def factory(_url: str) -> _FakeAliyunWSConnection:
        return _FakeAliyunWSConnection(channel)

    return AliyunASRProvider(
        access_key_id="ak",
        access_key_secret="secret",
        app_key="app",
        ws_url="wss://example.invalid/ws",
        token_url="https://nls-meta.example/",
        ws_factory=factory,
        token_cache=AliyunTokenCache(cached_token=AccessToken(token="tk", expires_at=9999999999)),
    )


def test_aliyun_provider_drives_bridge_end_to_end(client: TestClient) -> None:
    """Full slice: AliyunASRProvider (real adapter, fake WS conn) →
    `_stream_asr_events` → WS envelope. Locks down that the adapter's
    `ASREvent(partial|final)` shape lines up with what the bridge
    expects to forward as `asr_partial`/`asr_final` JSON."""
    service, repo = _build_service()
    app.dependency_overrides[get_copilot_service] = lambda: service
    _seed_pending(repo, "cop_test0000000001")
    _install_asr(
        _aliyun_provider_with_scripted_events(
            partials=("你", "你好"),
            final="你好世界",
        )
    )

    with client.websocket_connect("/v1/copilot/sessions/cop_test0000000001/stream") as ws:
        ws.send_bytes(b"\x00\x01\x02")
        ws.send_text(_AUDIO_END_FRAME)
        events = [ws.receive_json() for _ in range(4)]

    assert events == [
        {"type": "asr_partial", "text": "你"},
        {"type": "asr_partial", "text": "你好"},
        {"type": "asr_final", "text": "你好世界"},
        _ALLOW_MOD,
    ]


# --------------------------------------------------------------------- #
# A-31: asr_status / asr_error trace tags for outage triage             #
# --------------------------------------------------------------------- #


def _final_tag_set(trace_mock: MagicMock) -> list[str]:
    """Return the cumulative tag list from the last `update(tags=...)` call,
    or `None` if the trace was never re-tagged. Langfuse v2 REPLACEs on
    each update so the final call holds the union."""
    tag_calls = [c for c in trace_mock.update.call_args_list if "tags" in c.kwargs]
    assert tag_calls, "expected at least one tags= update on the trace"
    return list(tag_calls[-1].kwargs["tags"])


def test_asr_status_ok_tag_added_on_successful_utterance(client: TestClient) -> None:
    """Every successful ASR call must tag `asr_status:ok`. Without this
    tag, the only way an analyst can tell a successful empty utterance
    apart from an ASR failure is by parsing structured logs — too
    coarse for on-call triage."""
    _lf_client, trace = _install_langfuse_mock()
    service, repo = _build_service()
    app.dependency_overrides[get_copilot_service] = lambda: service
    _seed_pending(repo, "cop_test0000000001")

    with client.websocket_connect("/v1/copilot/sessions/cop_test0000000001/stream") as ws:
        ws.send_bytes(b"hi")
        ws.send_text(_AUDIO_END_FRAME)
        for _ in range(3):
            ws.receive_json()  # asr_partial + asr_final + moderation

    tags = _final_tag_set(trace)
    assert "asr_status:ok" in tags
    assert not any(t.startswith("asr_error:") for t in tags)


@pytest.mark.parametrize(
    ("error", "expected_class"),
    [
        (ASRAuthError("token revoked", provider="aliyun"), "auth"),
        (
            ASRUpstreamError("upstream 502", provider="aliyun", status_code=502),
            "upstream",
        ),
        (ASRTimeoutError("vendor slow", provider="aliyun"), "timeout"),
    ],
    ids=["auth", "upstream", "timeout"],
)
def test_asr_failure_tags_status_failed_plus_error_class(
    client: TestClient,
    error: ASRError,
    expected_class: str,
) -> None:
    """One ASR failure variant per row. Each must produce both
    `asr_status:failed` AND `asr_error:{class}` so an analyst can
    filter the Langfuse UI for `asr_error:auth` to surface only
    the cred-rotation incidents, etc."""
    _lf_client, trace = _install_langfuse_mock()
    service, repo = _build_service()
    app.dependency_overrides[get_copilot_service] = lambda: service
    _seed_pending(repo, "cop_test0000000001")
    _install_asr(_FailingASRProvider(error))

    with client.websocket_connect("/v1/copilot/sessions/cop_test0000000001/stream") as ws:
        ws.send_bytes(b"hi")
        ws.send_text(_AUDIO_END_FRAME)
        ws.receive_json()  # asr_error envelope

    tags = _final_tag_set(trace)
    assert "asr_status:failed" in tags
    assert f"asr_error:{expected_class}" in tags
    # Negative: must NOT have asr_status:ok (mutual exclusion)
    assert "asr_status:ok" not in tags


def test_no_verdict_tag_when_asr_fails(client: TestClient) -> None:
    """ASR failure → empty final_text → no moderation runs → no
    `verdict:*` tag. The trace surface should ONLY show asr_status +
    asr_error, never a spurious `verdict:allow` for a no-op turn."""
    _lf_client, trace = _install_langfuse_mock()
    service, repo = _build_service()
    app.dependency_overrides[get_copilot_service] = lambda: service
    _seed_pending(repo, "cop_test0000000001")
    _install_asr(_FailingASRProvider(ASRAuthError("nope", provider="aliyun")))

    with client.websocket_connect("/v1/copilot/sessions/cop_test0000000001/stream") as ws:
        ws.send_bytes(b"x")
        ws.send_text(_AUDIO_END_FRAME)
        ws.receive_json()

    tags = _final_tag_set(trace)
    assert not any(t.startswith("verdict:") for t in tags)


# --------------------------------------------------------------------- #
# A-32: hint output moderation + verdict_output trace tag               #
# --------------------------------------------------------------------- #


def _install_scripted_moderation(
    *,
    output_verdict: str = "allow",
    output_backend_fails: bool = False,
) -> None:
    """Install a moderation service that returns `allow` on user_input
    (so the upstream gate doesn't block the hint from spawning) and
    a scripted decision on `ai_output` (the hint moderation A-32 adds).

    `output_backend_fails=True` raises `ModerationBackendError` on the
    ai_output call so the treat-as-allow-but-no-tag path is covered.
    """

    class _ScriptedBackend:
        name = "test_scripted"

        async def evaluate(self, content: str, context: str) -> Decision:
            _ = content
            if context == "ai_output":
                if output_backend_fails:
                    raise ModerationBackendError(
                        "scripted hint backend failure",
                        backend="test_scripted",
                    )
                effective = output_verdict
            else:
                effective = "allow"
            categories: tuple[str, ...] = ("other",) if effective != "allow" else ()
            redirect_resource = (
                RedirectResource(title="test", url="https://example.invalid")
                if effective == "redirect"
                else None
            )
            return Decision(
                verdict=effective,  # type: ignore[arg-type]
                score=0.9,
                categories=categories,  # type: ignore[arg-type]
                redirect_resource=redirect_resource,
            )

    service = ModerationService(backend=_ScriptedBackend(), event_sink=LogOnlyEventSink())
    app.dependency_overrides[get_moderation_service] = lambda: service


def test_hint_output_allow_emits_hint_done_and_tags_verdict_output(
    client: TestClient,
) -> None:
    """Default path: hint mod returns `allow`. `hint_done` ships normally
    and the trace gets `verdict_output:allow` mid-trace so analysts
    can sanity-check that the moderation pass actually ran."""
    _install_scripted_moderation(output_verdict="allow")
    _install_llm(chunks=("ok ", "advice"))
    _lf_client, trace = _install_langfuse_mock()
    service, repo = _build_service()
    app.dependency_overrides[get_copilot_service] = lambda: service
    _seed_pending(repo, "cop_test0000000001")

    with client.websocket_connect("/v1/copilot/sessions/cop_test0000000001/stream") as ws:
        ws.send_bytes(b"hi")
        ws.send_text(_AUDIO_END_FRAME)
        events = [ws.receive_json() for _ in range(6)]
        # asr_partial + asr_final + moderation + hint_delta x2 + hint_done

    assert {"type": "hint_done", "text": "ok advice"} in events
    tags = _final_tag_set(trace)
    assert "verdict_output:allow" in tags


def test_hint_output_block_emits_hint_error_instead_of_hint_done(
    client: TestClient,
) -> None:
    """`block` from the hint mod suppresses `hint_done` and emits
    `hint_error` instead. The deltas already streamed (we can't
    unsend), but the authoritative terminal frame is the error
    envelope so the frontend doesn't treat the bad content as
    canonical advice."""
    _install_scripted_moderation(output_verdict="block")
    _install_llm(chunks=("would not", " be advice"))
    _lf_client, trace = _install_langfuse_mock()
    service, repo = _build_service()
    app.dependency_overrides[get_copilot_service] = lambda: service
    _seed_pending(repo, "cop_test0000000001")

    with client.websocket_connect("/v1/copilot/sessions/cop_test0000000001/stream") as ws:
        ws.send_bytes(b"hi")
        ws.send_text(_AUDIO_END_FRAME)
        events = [ws.receive_json() for _ in range(6)]
        # asr_partial + asr_final + moderation + hint_delta x2 + hint_error

    # The terminal hint event must be `hint_error`, NOT `hint_done`.
    assert {"type": "hint_error", "message": "hint unavailable"} in events
    assert not any(e.get("type") == "hint_done" for e in events)
    tags = _final_tag_set(trace)
    assert "verdict_output:block" in tags


def test_hint_output_redirect_also_emits_hint_error(client: TestClient) -> None:
    """`redirect` on coaching advice is rare but mirrors block —
    suppress the hint, emit hint_error, tag the trace."""
    _install_scripted_moderation(output_verdict="redirect")
    _install_llm(chunks=("would not show",))
    _lf_client, trace = _install_langfuse_mock()
    service, repo = _build_service()
    app.dependency_overrides[get_copilot_service] = lambda: service
    _seed_pending(repo, "cop_test0000000001")

    with client.websocket_connect("/v1/copilot/sessions/cop_test0000000001/stream") as ws:
        ws.send_bytes(b"hi")
        ws.send_text(_AUDIO_END_FRAME)
        events = [ws.receive_json() for _ in range(5)]
        # asr_partial + asr_final + moderation + hint_delta + hint_error

    assert {"type": "hint_error", "message": "hint unavailable"} in events
    assert not any(e.get("type") == "hint_done" for e in events)
    tags = _final_tag_set(trace)
    assert "verdict_output:redirect" in tags


def test_hint_output_warn_does_not_gate_hint_done(client: TestClient) -> None:
    """`warn` is non-gating — the hint still ships as `hint_done`,
    matching how user-input `warn` doesn't block the user from
    talking. The verdict_output tag goes on for observability."""
    _install_scripted_moderation(output_verdict="warn")
    _install_llm(chunks=("borderline ", "advice"))
    _lf_client, trace = _install_langfuse_mock()
    service, repo = _build_service()
    app.dependency_overrides[get_copilot_service] = lambda: service
    _seed_pending(repo, "cop_test0000000001")

    with client.websocket_connect("/v1/copilot/sessions/cop_test0000000001/stream") as ws:
        ws.send_bytes(b"hi")
        ws.send_text(_AUDIO_END_FRAME)
        events = [ws.receive_json() for _ in range(6)]

    assert {"type": "hint_done", "text": "borderline advice"} in events
    tags = _final_tag_set(trace)
    assert "verdict_output:warn" in tags


def test_hint_output_backend_failure_falls_through_to_hint_done(
    client: TestClient,
) -> None:
    """When the moderation backend itself fails on the hint check, we
    treat-as-allow (hint ships as `hint_done`) AND do NOT tag the
    trace — a false `verdict_output:allow` would hide the silent
    window from analysts triaging an outage."""
    _install_scripted_moderation(output_backend_fails=True)
    _install_llm(chunks=("here you go",))
    _lf_client, trace = _install_langfuse_mock()
    service, repo = _build_service()
    app.dependency_overrides[get_copilot_service] = lambda: service
    _seed_pending(repo, "cop_test0000000001")

    with client.websocket_connect("/v1/copilot/sessions/cop_test0000000001/stream") as ws:
        ws.send_bytes(b"hi")
        ws.send_text(_AUDIO_END_FRAME)
        events = [ws.receive_json() for _ in range(5)]

    assert {"type": "hint_done", "text": "here you go"} in events
    tags = _final_tag_set(trace)
    assert not any(t.startswith("verdict_output:") for t in tags)
