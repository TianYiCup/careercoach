"""Realtime copilot endpoints — PRD §7.5.

A-8 wired the POST stub with the `require_adult` compliance gate.
A-15 replaced the 501 with the real handler that mints a copilot_id,
persists a `pending` row, and returns the canonical WebSocket URL the
client connects to.
A-16 shipped the WS endpoint at that URL with an echo-loop scaffold.
A-18 (this module) replaces the echo with the real audio bridge:
bytes frames carry audio, the text control frame
`{"type":"audio_end"}` terminates each utterance, and the server
emits `asr_partial` / `asr_final` events back through `ASRProvider`.

Compliance attach point (POST)
------------------------------
`require_adult` chains through `require_age_set`, so the failure
ladder is:
  * No token                  → 401 UNAUTHORIZED
  * Token, age_set=False      → 403 AGE_REQUIRED
  * Token, is_minor=True      → 403 MINOR_FORBIDDEN
  * Token, adult, age set     → 200 (real handler runs)

R-15 in PRD §11.2 (未成年人误用副驾) is the load-bearing constraint
that makes the minor gate non-negotiable.

WebSocket auth model (transitional, A-16)
-----------------------------------------
The WS endpoint does NOT re-run `require_adult`. The capability ticket
is the freshly-minted `copilot_id` itself: only a `pending` row can
connect, and `pending` rows can only exist if the POST cleared the
adult gate. A future PR will tighten this with a JWT-bearing
subprotocol once B's client is comfortable signing the WS upgrade.
The 64 bits of entropy on `copilot_id` (16 hex chars) keeps brute-force
mining of valid ids out of reach for v0.

WS protocol — A-18 audio bridge
-------------------------------
Once connected, the client–server protocol is:

    Client → Server
        binary frame: PCM/Opus audio chunk for the current utterance
        text frame:   JSON `{"type":"audio_end"}` to finalize the
                      current utterance; any other text is ignored

    Server → Client
        text frame:   JSON envelope `{"type":"asr_partial","text":...}`
                      cumulative interim transcript, may revise
        text frame:   JSON envelope `{"type":"asr_final","text":...}`
                      exactly one per utterance, terminal transcript

Multiple utterances are supported per connection — the bridge loops
after each `audio_end`. A future PR adds `{"type":"hint","text":...}`
events from the Coach K agent alongside the ASR events.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from typing import Any

import structlog
from fastapi import APIRouter, Depends, WebSocket
from starlette.websockets import WebSocketDisconnect

from app.asr import ASRProvider, get_asr_provider
from app.schemas.copilot import CreateCopilotSessionRequest, CreateCopilotSessionResponse
from app.services.auth import CurrentUser, require_adult
from app.services.copilot import CopilotService, get_copilot_service
from app.services.copilot.service import (
    CopilotSessionNotFound,
    CopilotSessionUnavailable,
)

logger = structlog.get_logger(__name__)

router = APIRouter(prefix="/copilot", tags=["copilot"])

# Application-defined close codes (RFC 6455 §7.4.2 reserves 4000–4999
# for app use). 4404 / 4409 mirror the HTTP status semantics so any
# client that already understands "404 / 409 = retry policy" can map
# them mechanically without a per-feature lookup table.
_WS_UNKNOWN_COPILOT_CODE = 4404
_WS_SESSION_ALREADY_USED_CODE = 4409

_AUDIO_END_TYPE = "audio_end"


@router.post(
    "/sessions",
    response_model=CreateCopilotSessionResponse,
    summary="Create a realtime copilot session",
    responses={
        401: {"description": "Missing or invalid bearer token."},
        403: {
            "description": (
                "`AGE_REQUIRED` if the JWT lacks `age_set=true`; "
                "`MINOR_FORBIDDEN` if the caller is under 18. PRD §1.5 / R-15."
            ),
        },
    },
)
async def create_copilot_session(
    payload: CreateCopilotSessionRequest,
    current: CurrentUser = Depends(require_adult),
    service: CopilotService = Depends(get_copilot_service),
) -> CreateCopilotSessionResponse:
    """Mint a copilot session row and return the WebSocket URL.

    No LLM call, no moderation in this handler — copilot is
    adult-only (gate-enforced) and the audio + transcript moderation
    will live in the WS handler once the cascading-mod pass is wired
    against the ASR final events. A-15 persists the session intent so
    the WS endpoint can look it up on connect.
    """
    result = await service.create_session(
        scenario_hint=payload.scenario_hint,
        privacy_level=payload.privacy_level,
        user_id=current.user_id,
    )
    return CreateCopilotSessionResponse(
        copilot_id=result.record.copilot_id,
        ws_url=result.ws_url,
    )


@router.websocket("/sessions/{copilot_id}/stream")
async def copilot_stream(
    websocket: WebSocket,
    copilot_id: str,
    service: CopilotService = Depends(get_copilot_service),
    asr_provider: ASRProvider = Depends(get_asr_provider),
) -> None:
    """Realtime copilot stream — A-18 audio bridge.

    Lifecycle
    ---------
    1. Accept the WS upgrade unconditionally — we always accept first
       so failures send a structured close frame instead of the opaque
       `403` that pre-accept rejection would yield. Clients then know
       how to map our 4xxx codes.
    2. `connect_session` flips the row to `connected` (or raises one
       of the two error types below).
    3. Multi-utterance loop: for each utterance, run the audio bridge
       until `audio_end` or disconnect.
    4. Client disconnect (or any exception) runs `end_session` in
       `finally` — flips the row to `ended` and stamps `ended_at`.

    The `connected` flag below is the gating signal for the cleanup:
    we only call `end_session` if `connect_session` actually succeeded.
    Otherwise an "unknown id" probe would prematurely end somebody
    else's session (or a no-op write that clutters the audit log).
    """
    await websocket.accept()
    connected = False
    try:
        await service.connect_session(copilot_id)
        connected = True
    except CopilotSessionNotFound:
        await websocket.close(
            code=_WS_UNKNOWN_COPILOT_CODE,
            reason="UNKNOWN_COPILOT",
        )
        return
    except CopilotSessionUnavailable:
        await websocket.close(
            code=_WS_SESSION_ALREADY_USED_CODE,
            reason="SESSION_ALREADY_USED",
        )
        return

    try:
        while True:
            await _run_one_utterance(websocket, asr_provider, copilot_id)
    except WebSocketDisconnect:
        logger.info("copilot_ws_disconnected", copilot_id=copilot_id)
    finally:
        if connected:
            await service.end_session(copilot_id)


async def _run_one_utterance(
    websocket: WebSocket,
    asr_provider: ASRProvider,
    copilot_id: str,
) -> None:
    """Receive audio bytes + an `audio_end` control frame, transcribe,
    emit `asr_partial` / `asr_final` events back. Returns cleanly when
    the utterance ends; raises `WebSocketDisconnect` if the client
    disconnects mid-utterance.

    Concurrency: feeder and streamer run in parallel via
    `asyncio.gather` because `DummyASRProvider` (and any real vendor)
    yields partials as chunks arrive — we can't feed first then
    stream, or partials would back up unbounded. `gather` cancels the
    other task on first exception, which is the right behavior for a
    `WebSocketDisconnect` from either side.
    """
    audio_queue: asyncio.Queue[bytes | None] = asyncio.Queue()

    async def audio_iter() -> AsyncIterator[bytes]:
        while True:
            chunk = await audio_queue.get()
            if chunk is None:
                return
            yield chunk

    feeder = _feed_audio(websocket, audio_queue, copilot_id)
    streamer = _stream_asr_events(websocket, asr_provider, audio_iter(), copilot_id)
    await asyncio.gather(feeder, streamer)


async def _feed_audio(
    websocket: WebSocket,
    audio_queue: asyncio.Queue[bytes | None],
    copilot_id: str,
) -> None:
    """Pull frames off the WS and feed audio bytes into the queue.
    Returns when `audio_end` arrives; raises `WebSocketDisconnect` on
    client disconnect (and puts a None sentinel so the ASR iterator
    unblocks before we propagate)."""
    while True:
        msg = await websocket.receive()
        if msg["type"] == "websocket.disconnect":
            # End-of-stream sentinel for the ASR iterator + raise so
            # the streamer task gets cancelled and the outer handler
            # logs + cleans up.
            await audio_queue.put(None)
            raise WebSocketDisconnect(code=msg.get("code", 1000))

        chunk: bytes | None = msg.get("bytes")
        if chunk is not None:
            await audio_queue.put(chunk)
            continue

        text: str | None = msg.get("text")
        if text is not None and _is_audio_end(text):
            await audio_queue.put(None)
            return
        # Any other text frame is silently ignored — tightening to
        # "raise on unknown" later is easy; loosening from raise to
        # ignore would break existing clients.
        if text is not None:
            logger.debug(
                "copilot_ws_unknown_text_frame",
                copilot_id=copilot_id,
                preview=text[:80],
            )


async def _stream_asr_events(
    websocket: WebSocket,
    asr_provider: ASRProvider,
    audio_chunks: AsyncIterator[bytes],
    copilot_id: str,
) -> None:
    """Consume the ASR provider's event stream and forward each event
    as a typed WS envelope. The envelope shape mirrors the SSE
    discriminator used by `/sessions/{id}/turns`: a top-level `type`
    field that the client switches on."""
    async for event in asr_provider.transcribe_stream(audio_chunks):
        await websocket.send_json(
            {
                "type": f"asr_{event.kind}",
                "text": event.text,
            }
        )
    logger.info(
        "copilot_ws_utterance_complete",
        copilot_id=copilot_id,
        provider=asr_provider.name,
    )


def _is_audio_end(raw: str) -> bool:
    """`{"type":"audio_end"}` is the only text control frame the
    bridge currently recognises. Robust against malformed JSON and
    unexpected shapes — anything we don't understand is silently
    dropped at the call site."""
    try:
        data: Any = json.loads(raw)
    except json.JSONDecodeError:
        return False
    return isinstance(data, dict) and data.get("type") == _AUDIO_END_TYPE
