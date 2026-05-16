"""WebSocket scaffold tests for `/v1/copilot/sessions/{copilot_id}/stream`.

A-16 wires the WS endpoint that pairs with A-15's POST + persistence.
This is a SCAFFOLD: the only "logic" inside the connection is an echo
loop. A later PR replaces the echo with real ASR + LLM-hint streaming
(the package was originally penciled in as A-17 — order flipped so the
ASR adapter can land after we know the WS handshake shape).

What's locked here (do not regress)
-----------------------------------
    happy path        → mark_connected on accept, echo loop, mark_ended on close
    unknown copilot_id → WS close 4404 (UNKNOWN_COPILOT)
    already-used id    → WS close 4409 (SESSION_ALREADY_USED)
    user_id attribution stays the JWT-derived value from the POST
"""

from __future__ import annotations

import asyncio
from collections.abc import Iterator
from datetime import UTC, datetime
from itertools import count

import pytest
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
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


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


def test_echo_loop_round_trips_text(client: TestClient) -> None:
    """The scaffold echoes whatever the client sends, wrapped in a
    typed envelope. A-16's contract is `{"type": "echo", "text": ...}`
    so the future real-hint payloads can keep the same envelope and
    just add new `type` values without breaking the client."""
    service, repo = _build_service()
    app.dependency_overrides[get_copilot_service] = lambda: service
    _seed_pending(repo, "cop_test0000000001")

    with client.websocket_connect("/v1/copilot/sessions/cop_test0000000001/stream") as ws:
        ws.send_text("hello K")
        msg = ws.receive_json()

    assert msg == {"type": "echo", "text": "hello K"}


def test_multiple_messages_all_echoed_in_order(client: TestClient) -> None:
    """The receive→send loop runs forever until disconnect. Three
    messages in, three echoes out, in order."""
    service, repo = _build_service()
    app.dependency_overrides[get_copilot_service] = lambda: service
    _seed_pending(repo, "cop_test0000000001")

    sent = ["one", "two", "three"]
    received: list[dict[str, str]] = []
    with client.websocket_connect("/v1/copilot/sessions/cop_test0000000001/stream") as ws:
        for text in sent:
            ws.send_text(text)
            received.append(ws.receive_json())

    assert received == [{"type": "echo", "text": t} for t in sent]


def test_client_disconnect_flips_status_to_ended(client: TestClient) -> None:
    """On WebSocketDisconnect (client closes), the server calls
    mark_ended so a reader sees the row in `ended` state with
    `ended_at` populated. Cleanup must run in the handler's finally
    block — see the WS endpoint for why."""
    service, repo = _build_service()
    app.dependency_overrides[get_copilot_service] = lambda: service
    _seed_pending(repo, "cop_test0000000001")

    with client.websocket_connect("/v1/copilot/sessions/cop_test0000000001/stream") as ws:
        ws.send_text("noop")
        ws.receive_json()
    # Context exit closes the WS — server's finally block flips status.

    final = asyncio.run(repo.get("cop_test0000000001"))
    assert final is not None
    assert final.status == "ended"
    assert final.connected_at is not None
    assert final.ended_at is not None
    # ended_at strictly after connected_at because each clock() call
    # advances the deterministic counter.
    assert final.ended_at > final.connected_at


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
