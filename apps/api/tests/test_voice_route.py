"""HTTP-level tests for `POST /v1/sessions/{id}/voice` (US-A3).

A voice turn = ASR transcription + the identical `/turns` pipeline.
`DummyASRProvider` echoes UTF-8 bytes, so an uploaded UTF-8 blob round-
trips as its own transcript — letting these tests assert the SSE
contract without real audio fixtures.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterator

import pytest
from app.asr.dummy import DummyASRProvider
from app.main import app
from app.routes.v1.sessions import _MAX_VOICE_UPLOAD_BYTES
from app.services.auth import mint_token
from app.services.sessions import (
    VoiceTranscriptionService,
    get_session_service,
    get_turn_service,
    get_voice_transcription_service,
)
from httpx import ASGITransport, AsyncClient

from tests.test_sessions_turns_route import _build_services, _create_session, _parse_sse
from tests.test_voice_transcription_service import _FailingASR


def _dummy_voice_service() -> VoiceTranscriptionService:
    return VoiceTranscriptionService(asr=DummyASRProvider())


def _failing_voice_service() -> VoiceTranscriptionService:
    return VoiceTranscriptionService(asr=_FailingASR())


def _override(
    *,
    block_moderation: bool = False,
    redirect_moderation: bool = False,
    failing_asr: bool = False,
) -> Iterator[None]:
    """Wire dummy session / turn / voice services for one test.

    The voice ASR is `DummyASRProvider` unless `failing_asr` — which
    swaps in an always-raising provider to exercise the 502 path.
    """
    session_svc, turn_svc = _build_services(
        block_moderation=block_moderation,
        redirect_moderation=redirect_moderation,
    )
    voice_factory = _failing_voice_service if failing_asr else _dummy_voice_service
    app.dependency_overrides[get_session_service] = lambda: session_svc
    app.dependency_overrides[get_turn_service] = lambda: turn_svc
    app.dependency_overrides[get_voice_transcription_service] = voice_factory
    try:
        yield
    finally:
        for dep in (
            get_session_service,
            get_turn_service,
            get_voice_transcription_service,
        ):
            app.dependency_overrides.pop(dep, None)


@pytest.fixture
def voice_override() -> Iterator[None]:
    yield from _override()


@pytest.fixture
def blocking_voice_override() -> Iterator[None]:
    yield from _override(block_moderation=True)


@pytest.fixture
def redirecting_voice_override() -> Iterator[None]:
    yield from _override(redirect_moderation=True)


@pytest.fixture
def failing_asr_override() -> Iterator[None]:
    yield from _override(failing_asr=True)


@pytest.fixture
async def client() -> AsyncIterator[AsyncClient]:
    """Default-authenticated client — `/voice` is auth-gated."""
    token = mint_token(
        user_id="u_voice_test",
        persona_type="intern",
        is_minor=False,
        age_set=True,
    )
    transport = ASGITransport(app=app)
    async with AsyncClient(
        transport=transport,
        base_url="http://test",
        headers={"Authorization": f"Bearer {token}"},
    ) as ac:
        yield ac


def _audio(text: str) -> dict[str, tuple[str, bytes, str]]:
    """A multipart `audio` file whose bytes the dummy ASR transcribes
    back to `text`."""
    return {"audio": ("turn.wav", text.encode(), "audio/wav")}


async def test_voice_turn_streams_transcribed_then_reply(
    client: AsyncClient, voice_override: None
) -> None:
    """Happy path: the first frame is `user.transcribed`, then the
    identical opponent / coach / meta frames a typed turn emits."""
    _ = voice_override
    session_id = await _create_session(client)

    resp = await client.post(
        f"/v1/sessions/{session_id}/voice",
        files=_audio("赵总，我周末有重要安排"),
    )

    assert resp.status_code == 200, resp.text
    assert resp.headers["content-type"].startswith("text/event-stream")
    frames = _parse_sse(resp.text)
    events = [name for name, _ in frames]

    assert events[0] == "user.transcribed"
    assert frames[0][1]["text"] == "赵总，我周末有重要安排"
    assert "opponent.done" in events
    assert "coach.hint" in events
    assert "meta" in events


async def test_voice_turn_requires_auth(voice_override: None) -> None:
    _ = voice_override
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        resp = await ac.post("/v1/sessions/ses_x/voice", files=_audio("你好"))
    assert resp.status_code == 401


async def test_voice_turn_rejects_empty_audio(client: AsyncClient, voice_override: None) -> None:
    _ = voice_override
    resp = await client.post(
        "/v1/sessions/ses_x/voice",
        files={"audio": ("turn.wav", b"", "audio/wav")},
    )
    assert resp.status_code == 422
    assert resp.json()["code"] == "EMPTY_AUDIO"


async def test_voice_turn_rejects_empty_transcription(
    client: AsyncClient, voice_override: None
) -> None:
    """Audio that transcribes to whitespace → PRD US-A3 '没听清'. The
    422 fires before any session lookup, so an unknown session id is
    fine here."""
    _ = voice_override
    resp = await client.post(
        "/v1/sessions/ses_x/voice",
        files=_audio("   "),
    )
    assert resp.status_code == 422
    assert resp.json()["code"] == "EMPTY_TRANSCRIPTION"


async def test_voice_turn_rejects_oversized_audio(
    client: AsyncClient, voice_override: None
) -> None:
    _ = voice_override
    oversized = b"x" * (_MAX_VOICE_UPLOAD_BYTES + 1)
    resp = await client.post(
        "/v1/sessions/ses_x/voice",
        files={"audio": ("turn.wav", oversized, "audio/wav")},
    )
    assert resp.status_code == 413
    assert resp.json()["code"] == "AUDIO_TOO_LARGE"


async def test_voice_turn_returns_404_for_unknown_session(
    client: AsyncClient, voice_override: None
) -> None:
    _ = voice_override
    resp = await client.post(
        "/v1/sessions/ses_never/voice",
        files=_audio("你好赵总"),
    )
    assert resp.status_code == 404
    assert resp.json()["code"] == "NOT_FOUND"


async def test_voice_turn_returns_409_for_ended_session(
    client: AsyncClient, voice_override: None
) -> None:
    _ = voice_override
    session_id = await _create_session(client)
    end_resp = await client.post(f"/v1/sessions/{session_id}/end")
    assert end_resp.status_code == 200

    resp = await client.post(
        f"/v1/sessions/{session_id}/voice",
        files=_audio("还能再聊吗"),
    )
    assert resp.status_code == 409
    assert resp.json()["code"] == "ALREADY_ENDED"


async def test_voice_turn_returns_400_when_moderation_blocks(
    client: AsyncClient, blocking_voice_override: None
) -> None:
    _ = blocking_voice_override
    session_id = await _create_session(client)
    resp = await client.post(
        f"/v1/sessions/{session_id}/voice",
        files=_audio("一段会被审核拦下的话"),
    )
    assert resp.status_code == 400
    assert resp.json()["code"] == "USER_INPUT_BLOCKED"


async def test_voice_turn_redirect_emits_moderation_frame_only(
    client: AsyncClient, redirecting_voice_override: None
) -> None:
    """A `redirect` verdict (self-harm) streams a lone `moderation`
    frame — no `user.transcribed` first, since that frame must be the
    only frame in the stream (H-1)."""
    _ = redirecting_voice_override
    session_id = await _create_session(client)
    resp = await client.post(
        f"/v1/sessions/{session_id}/voice",
        files=_audio("我不想活了"),
    )
    assert resp.status_code == 200
    frames = _parse_sse(resp.text)
    events = [name for name, _ in frames]
    assert events == ["moderation"]
    assert frames[0][1]["verdict"] == "redirect"


async def test_voice_turn_returns_502_when_asr_unavailable(
    client: AsyncClient, failing_asr_override: None
) -> None:
    _ = failing_asr_override
    resp = await client.post(
        "/v1/sessions/ses_x/voice",
        files=_audio("你好"),
    )
    assert resp.status_code == 502
    assert resp.json()["code"] == "ASR_UNAVAILABLE"
