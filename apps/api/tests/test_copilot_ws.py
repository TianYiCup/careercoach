"""WebSocket audio-bridge + moderation tests for the copilot stream.

A-16 shipped the WS scaffold with an echo loop; A-18 replaced the
echo with the audio bridge; A-19 chains moderation after each
`asr_final` so the client (and any future Coach K wiring) can see the
red-line verdict for the finalized transcript.

What's locked here (do not regress)
-----------------------------------
    handshake          → mark_connected (status flips, connected_at populated)
    audio bytes        → DummyASR emits a partial per chunk
    audio_end frame    → DummyASR emits exactly one final
    asr_final w/ text  → moderation event follows with verdict / score
    empty asr_final    → moderation event omitted (nothing to score)
    multi-utterance    → second audio_end starts the next utterance
    client disconnect  → mark_ended (status flips, ended_at populated)
    unknown copilot_id → WS close 4404 (UNKNOWN_COPILOT)
    already-used id    → WS close 4409 (SESSION_ALREADY_USED)
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Iterator
from datetime import UTC, datetime
from itertools import count

import pytest
from app.asr import get_asr_provider
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

# `allow` payload the dummy text "ok" / "hello world" produces against
# the bundled red-line dict — none of those substrings match.
_ALLOW_MOD = {
    "type": "moderation",
    "verdict": "allow",
    "categories": [],
    "score": 0.0,
    "redirect_resource": None,
}


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
    """Drop a pending row into the repo, mimicking what POST /sessions
    persists. Bypasses the service so each test can name its own id."""
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

    `with_dict=False` (default) → `NoopBackend` so the moderation
    event is always `verdict=allow` and tests focused on the audio
    bridge don't have to assert on red-line content.

    `with_dict=True` → `DictBackend.from_file()` so tests can speak
    real red-line keywords ("想死" / "校园暴力") and assert on the
    `redirect` / `block` verdicts.

    Both wire `LogOnlyEventSink` so the audit row write doesn't
    require a postgres connection.
    """
    backend = DictBackend.from_file() if with_dict else NoopBackend()
    service = ModerationService(backend=backend, event_sink=LogOnlyEventSink())
    app.dependency_overrides[get_moderation_service] = lambda: service


@pytest.fixture
def client() -> Iterator[TestClient]:
    """Sync TestClient — WS support is sync-only in starlette. The
    async `httpx` fixture used by the POST tests can't speak the WS
    handshake protocol, so we keep this fixture parallel-but-separate.

    Defaults `get_moderation_service` to a `NoopBackend` so existing
    audio-bridge tests don't have to assert on moderation events that
    aren't load-bearing for them. Tests that DO care override via
    `_install_moderation(with_dict=True)`.
    """
    get_asr_provider.cache_clear()
    _install_moderation(with_dict=False)
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()
    get_asr_provider.cache_clear()


# --------------------------------------------------------------------- #
# Happy path — audio bridge                                              #
# --------------------------------------------------------------------- #


def test_handshake_flips_status_to_connected(client: TestClient) -> None:
    """On WS accept, the server calls `mark_connected` so a reader of
    the repo sees the row in `connected` state with `connected_at`
    populated."""
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
    moderation event. The moderation event always follows asr_final
    when the final text is non-empty."""
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
    """One chunk + audio_end → one partial + one final + one moderation."""
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
    """`audio_end` with zero bytes between → exactly one final with
    empty text + NO moderation event (Pydantic min_length=1 on the
    moderation request body would reject empty content; we skip the
    call entirely instead of bubbling the validation error)."""
    service, repo = _build_service()
    app.dependency_overrides[get_copilot_service] = lambda: service
    _seed_pending(repo, "cop_test0000000001")

    with client.websocket_connect("/v1/copilot/sessions/cop_test0000000001/stream") as ws:
        ws.send_text(_AUDIO_END_FRAME)
        # Receive the asr_final, then the next utterance immediately
        # after to confirm no moderation event was queued in between.
        first_final = ws.receive_json()
        ws.send_bytes(b"after")
        ws.send_text(_AUDIO_END_FRAME)
        next_partial = ws.receive_json()

    assert first_final == {"type": "asr_final", "text": ""}
    assert next_partial == {"type": "asr_partial", "text": "after"}


def test_multi_utterance_in_one_connection(client: TestClient) -> None:
    """Two utterances in one connection — the bridge loops cleanly
    after each `audio_end`. Each utterance gets its own moderation
    event."""
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
    """Text frames that aren't `audio_end` (or aren't valid JSON, or
    have an unknown `type`) are silently dropped."""
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
    """On WebSocketDisconnect, the server calls mark_ended so a reader
    sees the row in `ended` state with `ended_at` populated."""
    service, repo = _build_service()
    app.dependency_overrides[get_copilot_service] = lambda: service
    _seed_pending(repo, "cop_test0000000001")

    with client.websocket_connect("/v1/copilot/sessions/cop_test0000000001/stream") as ws:
        ws.send_bytes(b"noop")
        ws.send_text(_AUDIO_END_FRAME)
        # Drain the 3 expected events: partial, final, moderation.
        for _ in range(3):
            ws.receive_json()

    final = asyncio.run(repo.get("cop_test0000000001"))
    assert final is not None
    assert final.status == "ended"
    assert final.connected_at is not None
    assert final.ended_at is not None
    assert final.ended_at > final.connected_at


def test_disconnect_mid_utterance_still_flips_ended(client: TestClient) -> None:
    """Client disconnects without sending audio_end — server still
    runs mark_ended. The half-finished utterance is dropped (no
    finalize, no moderation event)."""
    service, repo = _build_service()
    app.dependency_overrides[get_copilot_service] = lambda: service
    _seed_pending(repo, "cop_test0000000001")

    with client.websocket_connect("/v1/copilot/sessions/cop_test0000000001/stream") as ws:
        ws.send_bytes(b"partial")
        # No audio_end — just close.

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
    # Shape-only check on the resource — title/url are policy not
    # contract, but presence + non-empty is the load-bearing thing.
    assert moderation["redirect_resource"] is not None
    assert moderation["redirect_resource"]["title"]
    assert moderation["redirect_resource"]["url"]


def test_red_line_text_emits_block_verdict(client: TestClient) -> None:
    """A finalized transcript containing a non-self-harm red-line
    keyword (`violence` / `loan` / etc.) must emit a moderation event
    with `verdict=block`. The client uses this to suppress any
    downstream forwarding (e.g. to Coach K once that lands)."""
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
    """Sanity: text that doesn't trip any red-line term yields
    `verdict=allow` even with the real DictBackend wired. Locks the
    contract that the moderation event always fires for non-empty
    finals, regardless of verdict."""
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
# Error paths — close-code contract                                      #
# --------------------------------------------------------------------- #


def test_unknown_copilot_id_closes_with_4404(client: TestClient) -> None:
    """Connecting to a copilot_id that was never POSTed must close the
    WS with code 4404."""
    service, _ = _build_service()
    app.dependency_overrides[get_copilot_service] = lambda: service

    with pytest.raises(WebSocketDisconnect) as exc_info:  # noqa: SIM117 — nested ctx is the test
        with client.websocket_connect(
            "/v1/copilot/sessions/cop_does_not_exist/stream",
        ) as ws:
            ws.receive_text()

    assert exc_info.value.code == 4404


def test_already_ended_session_closes_with_4409(client: TestClient) -> None:
    """Once a session has been used (connected → ended), a second
    connect attempt must close with 4409."""
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
    """A failed WS connect must not write to the repo."""
    service, repo = _build_service()
    app.dependency_overrides[get_copilot_service] = lambda: service

    with pytest.raises(WebSocketDisconnect):  # noqa: SIM117
        with client.websocket_connect(
            "/v1/copilot/sessions/cop_phantom/stream",
        ) as ws:
            ws.receive_text()

    assert asyncio.run(repo.get("cop_phantom")) is None
