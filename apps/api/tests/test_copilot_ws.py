"""WebSocket audio-bridge tests for `/v1/copilot/sessions/{copilot_id}/stream`.

A-16 shipped the scaffold with an echo loop. A-18 replaces the echo
with the real audio bridge: bytes frames carry audio, a text control
frame `{"type":"audio_end"}` terminates each utterance, and the server
emits `asr_partial` / `asr_final` events back to the client.

What's locked here (do not regress)
-----------------------------------
    handshake          → mark_connected (status flips, connected_at populated)
    audio bytes        → DummyASR emits a partial per chunk
    audio_end frame    → DummyASR emits exactly one final
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
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

_WS_BASE_URL = "ws://test.local"
_SEED_CREATED_AT = datetime(2026, 5, 16, 11, 59, tzinfo=UTC)
_AUDIO_END_FRAME = json.dumps({"type": "audio_end"})


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


@pytest.fixture
def client() -> Iterator[TestClient]:
    """Sync TestClient — WS support is sync-only in starlette. The
    async `httpx` fixture used by the POST tests can't speak the WS
    handshake protocol, so we keep this fixture parallel-but-separate."""
    # ASR factory is process-wide cached; clear so a stale dummy from
    # an earlier test doesn't leak into the next.
    get_asr_provider.cache_clear()
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()
    get_asr_provider.cache_clear()


# --------------------------------------------------------------------- #
# Happy path                                                             #
# --------------------------------------------------------------------- #


def test_handshake_flips_status_to_connected(client: TestClient) -> None:
    """On WS accept, the server calls `mark_connected` so a reader of
    the repo sees the row in `connected` state with `connected_at`
    populated. This is what the future detail-fetch route (and ops
    dashboards) will use to count live copilot sessions."""
    service, repo = _build_service()
    app.dependency_overrides[get_copilot_service] = lambda: service
    _seed_pending(repo, "cop_test0000000001")

    with client.websocket_connect("/v1/copilot/sessions/cop_test0000000001/stream"):
        # Mid-connection — server has already called mark_connected by the
        # time accept() returns to the client.
        mid = asyncio.run(repo.get("cop_test0000000001"))
        assert mid is not None
        assert mid.status == "connected"
        assert mid.connected_at is not None
        # ended_at is still None — we haven't closed yet.
        assert mid.ended_at is None


def test_single_utterance_emits_partial_then_final(client: TestClient) -> None:
    """Send two audio chunks then `audio_end`; expect two partials
    (cumulative transcript) followed by exactly one final. This is
    the contract a future real ASR vendor will match — DummyASR is
    just the in-process echo for tests + dev runs."""
    service, repo = _build_service()
    app.dependency_overrides[get_copilot_service] = lambda: service
    _seed_pending(repo, "cop_test0000000001")

    with client.websocket_connect("/v1/copilot/sessions/cop_test0000000001/stream") as ws:
        ws.send_bytes(b"hello ")
        ws.send_bytes(b"world")
        ws.send_text(_AUDIO_END_FRAME)
        events = [ws.receive_json() for _ in range(3)]

    assert events == [
        {"type": "asr_partial", "text": "hello "},
        {"type": "asr_partial", "text": "hello world"},
        {"type": "asr_final", "text": "hello world"},
    ]


def test_single_chunk_utterance(client: TestClient) -> None:
    """One chunk + audio_end → one partial + one final. The minimum
    viable utterance the bridge must handle."""
    service, repo = _build_service()
    app.dependency_overrides[get_copilot_service] = lambda: service
    _seed_pending(repo, "cop_test0000000001")

    with client.websocket_connect("/v1/copilot/sessions/cop_test0000000001/stream") as ws:
        ws.send_bytes(b"hi")
        ws.send_text(_AUDIO_END_FRAME)
        events = [ws.receive_json() for _ in range(2)]

    assert events == [
        {"type": "asr_partial", "text": "hi"},
        {"type": "asr_final", "text": "hi"},
    ]


def test_empty_utterance_emits_only_final(client: TestClient) -> None:
    """`audio_end` with zero bytes between → exactly one final with
    empty text. Real ASR vendors emit the same shape (silence still
    finalizes); the bridge must not collapse the event."""
    service, repo = _build_service()
    app.dependency_overrides[get_copilot_service] = lambda: service
    _seed_pending(repo, "cop_test0000000001")

    with client.websocket_connect("/v1/copilot/sessions/cop_test0000000001/stream") as ws:
        ws.send_text(_AUDIO_END_FRAME)
        event = ws.receive_json()

    assert event == {"type": "asr_final", "text": ""}


def test_multi_utterance_in_one_connection(client: TestClient) -> None:
    """Two utterances in one connection — the bridge loops cleanly
    after each `audio_end`. This is the steady-state usage pattern
    for the copilot session: many short utterances, one WS upgrade."""
    service, repo = _build_service()
    app.dependency_overrides[get_copilot_service] = lambda: service
    _seed_pending(repo, "cop_test0000000001")

    with client.websocket_connect("/v1/copilot/sessions/cop_test0000000001/stream") as ws:
        # Utterance 1
        ws.send_bytes(b"first")
        ws.send_text(_AUDIO_END_FRAME)
        first = [ws.receive_json() for _ in range(2)]

        # Utterance 2
        ws.send_bytes(b"second")
        ws.send_text(_AUDIO_END_FRAME)
        second = [ws.receive_json() for _ in range(2)]

    assert first == [
        {"type": "asr_partial", "text": "first"},
        {"type": "asr_final", "text": "first"},
    ]
    assert second == [
        {"type": "asr_partial", "text": "second"},
        {"type": "asr_final", "text": "second"},
    ]


def test_unknown_text_frames_are_ignored(client: TestClient) -> None:
    """A text frame that isn't `audio_end` (and isn't valid JSON, or
    is JSON with an unknown `type`) must be silently dropped. The
    bridge doesn't error on garbage from the client — it just keeps
    accumulating audio. Tightening to "raise on unknown" later is
    easy; loosening from "raise" to "ignore" later breaks clients."""
    service, repo = _build_service()
    app.dependency_overrides[get_copilot_service] = lambda: service
    _seed_pending(repo, "cop_test0000000001")

    with client.websocket_connect("/v1/copilot/sessions/cop_test0000000001/stream") as ws:
        ws.send_text("not even json")
        ws.send_text(json.dumps({"type": "something_else"}))
        ws.send_bytes(b"ok")
        ws.send_text(_AUDIO_END_FRAME)
        events = [ws.receive_json() for _ in range(2)]

    assert events == [
        {"type": "asr_partial", "text": "ok"},
        {"type": "asr_final", "text": "ok"},
    ]


def test_client_disconnect_flips_status_to_ended(client: TestClient) -> None:
    """On WebSocketDisconnect (client closes), the server calls
    mark_ended so a reader sees the row in `ended` state with
    `ended_at` populated. Cleanup must run in the handler's finally
    block — see the WS endpoint for why."""
    service, repo = _build_service()
    app.dependency_overrides[get_copilot_service] = lambda: service
    _seed_pending(repo, "cop_test0000000001")

    with client.websocket_connect("/v1/copilot/sessions/cop_test0000000001/stream") as ws:
        # Send + receive at least one utterance so we exercise the
        # multi-utterance loop path before disconnect.
        ws.send_bytes(b"noop")
        ws.send_text(_AUDIO_END_FRAME)
        ws.receive_json()
        ws.receive_json()
    # Context exit closes the WS — server's finally block flips status.

    final = asyncio.run(repo.get("cop_test0000000001"))
    assert final is not None
    assert final.status == "ended"
    assert final.connected_at is not None
    assert final.ended_at is not None
    assert final.ended_at > final.connected_at


def test_disconnect_mid_utterance_still_flips_ended(client: TestClient) -> None:
    """Client disconnects without sending audio_end — server still
    runs mark_ended. The half-finished utterance is dropped (we don't
    finalize on disconnect, only on audio_end). Future ASR vendors
    that emit on silence will get the same handling."""
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
# Error paths — close-code contract                                      #
# --------------------------------------------------------------------- #


def test_unknown_copilot_id_closes_with_4404(client: TestClient) -> None:
    """Connecting to a copilot_id that was never POSTed must close the
    WS with code 4404. The client uses this to distinguish "session
    expired or never existed" from a transport drop (which would surface
    as a generic 1006)."""
    service, _ = _build_service()
    app.dependency_overrides[get_copilot_service] = lambda: service

    with pytest.raises(WebSocketDisconnect) as exc_info:  # noqa: SIM117 — nested ctx is the test
        with client.websocket_connect(
            "/v1/copilot/sessions/cop_does_not_exist/stream",
        ) as ws:
            # Server closes after accept; the receive raises.
            ws.receive_text()

    assert exc_info.value.code == 4404


def test_already_ended_session_closes_with_4409(client: TestClient) -> None:
    """Once a session has been used (connected → ended), a second
    connect attempt must close with 4409. v0 has no session replay /
    reconnect — a dropped WS forces a new POST."""
    service, repo = _build_service()
    app.dependency_overrides[get_copilot_service] = lambda: service
    _seed_pending(repo, "cop_test0000000001")

    with client.websocket_connect("/v1/copilot/sessions/cop_test0000000001/stream"):
        pass  # First connect runs to completion → status flips to ended.

    with pytest.raises(WebSocketDisconnect) as exc_info:  # noqa: SIM117
        with client.websocket_connect(
            "/v1/copilot/sessions/cop_test0000000001/stream",
        ) as ws:
            ws.receive_text()

    assert exc_info.value.code == 4409


def test_unknown_copilot_id_does_not_mint_a_row(client: TestClient) -> None:
    """Belt-and-braces: a failed WS connect must not write to the repo.
    Otherwise an attacker could probe random ids and pollute the table
    (and the underlying analytics) with `ended` rows."""
    service, repo = _build_service()
    app.dependency_overrides[get_copilot_service] = lambda: service

    with pytest.raises(WebSocketDisconnect):  # noqa: SIM117
        with client.websocket_connect(
            "/v1/copilot/sessions/cop_phantom/stream",
        ) as ws:
            ws.receive_text()

    assert asyncio.run(repo.get("cop_phantom")) is None
