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

import pytest
from app.asr import get_asr_provider
from app.llm import LLMAuthError, Message
from app.llm.factory import get_llm_router
from app.llm.provider import DEFAULT_TEMPERATURE, DEFAULT_TIMEOUT_SECONDS
from app.main import app
from app.services.copilot import (
    CopilotService,
    CopilotSessionRecord,
    InMemoryCopilotRepository,
    get_copilot_service,
)
from app.services.moderation import (
    DictBackend,
    LogOnlyEventSink,
    ModerationService,
    NoopBackend,
    get_moderation_service,
)
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
    ) -> AsyncIterator[str]:
        _ = (messages, temperature, timeout)
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
) -> None:
    """Install a stub LLMRouter. Defaults to no-chunks so audio-bridge
    tests don't see hint events."""
    stub = _StubLLMRouter(chunks=chunks, raises=raises)
    app.dependency_overrides[get_llm_router] = lambda: stub


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
