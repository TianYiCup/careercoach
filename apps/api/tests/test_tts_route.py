"""HTTP-layer tests for `POST /v1/tts/synthesize` (PRD US-B2).

Compliance ladder (mirrors copilot — R-15):
  * no token              → 401 UNAUTHORIZED
  * age unset             → 403 AGE_REQUIRED
  * minor (age set)       → 403 MINOR_FORBIDDEN
  * adult (age set)       → 200 audio stream

Moderation:
  * `block` / `redirect`  → 422 TTS_INPUT_BLOCKED
  * backend outage        → 503 TTS_UNAVAILABLE
  * `allow` / `warn`      → 200 audio stream

Provider:
  * Returned bytes match the dummy/edge stub the test wired in
  * The stream sets `Content-Type: audio/mpeg` (or `audio/wav`)
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

import pytest
from app.main import app
from app.schemas.moderation import ModerationCheckResponse
from app.services.auth import mint_token
from app.services.moderation import (
    ModerationBackendError,
    ModerationService,
    get_moderation_service,
)
from app.tts import (
    TTSAudioChunk,
    TTSRouter,
    TTSUpstreamError,
    get_tts_router,
)
from app.tts.provider import (
    DEFAULT_AUDIO_FORMAT,
    DEFAULT_TIMEOUT_SECONDS,
    DEFAULT_VOICE,
    TTSAudioFormat,
    TTSVoice,
)
from httpx import ASGITransport, AsyncClient

# --------------------------------------------------------------------- #
# Fakes                                                                  #
# --------------------------------------------------------------------- #


class _StubModerationService:
    """ModerationService stub with controllable verdict + outage flag.

    Real `ModerationService.check` is async and accepts
    (payload, *, user_id, is_minor, trace_id). We mirror the signature
    so the route can call it unchanged.
    """

    def __init__(
        self,
        *,
        verdict: str = "allow",
        raises: BaseException | None = None,
    ) -> None:
        self._verdict = verdict
        self._raises = raises
        self.calls: list[dict[str, Any]] = []

    async def check(
        self,
        payload: Any,
        *,
        user_id: str,
        is_minor: bool,
        trace_id: str,
    ) -> ModerationCheckResponse:
        self.calls.append(
            {
                "content": payload.content,
                "user_id": user_id,
                "is_minor": is_minor,
                "trace_id": trace_id,
            }
        )
        if self._raises is not None:
            raise self._raises
        return ModerationCheckResponse(
            verdict=self._verdict,  # type: ignore[arg-type]
            score=0.1 if self._verdict == "allow" else 0.95,
            categories=[],
            redirect_resource=None,
            trace_id=trace_id,
        )


class _StubTTSProvider:
    """One-shot TTS provider that yields fixed audio bytes."""

    name = "stub"

    def __init__(self, *, audio: bytes = b"AUDIO") -> None:
        self._audio = audio
        self.calls: list[dict[str, Any]] = []

    def synthesize(
        self,
        text: str,
        *,
        voice: TTSVoice = DEFAULT_VOICE,
        audio_format: TTSAudioFormat = DEFAULT_AUDIO_FORMAT,
        timeout: float = DEFAULT_TIMEOUT_SECONDS,
    ) -> AsyncIterator[TTSAudioChunk]:
        self.calls.append({"text": text, "voice": voice, "format": audio_format})
        return self._gen()

    async def _gen(self) -> AsyncIterator[TTSAudioChunk]:
        yield TTSAudioChunk(audio=self._audio, is_final=False)
        yield TTSAudioChunk(audio=b"", is_final=True)


class _FailingTTSProvider:
    name = "failing"

    def synthesize(
        self,
        text: str,
        *,
        voice: TTSVoice = DEFAULT_VOICE,
        audio_format: TTSAudioFormat = DEFAULT_AUDIO_FORMAT,
        timeout: float = DEFAULT_TIMEOUT_SECONDS,
    ) -> AsyncIterator[TTSAudioChunk]:
        return self._gen()

    async def _gen(self) -> AsyncIterator[TTSAudioChunk]:
        if False:  # pragma: no cover — generator priming only
            yield TTSAudioChunk(audio=b"", is_final=True)
        raise TTSUpstreamError("vendor down", provider="stub")


# --------------------------------------------------------------------- #
# Fixtures                                                               #
# --------------------------------------------------------------------- #


@pytest.fixture
async def client() -> AsyncIterator[AsyncClient]:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac
    app.dependency_overrides.clear()


def _adult_token() -> str:
    return mint_token(
        user_id="u_adult",
        persona_type="intern",
        is_minor=False,
        age_set=True,
    )


def _wire_dependencies(
    *,
    moderation: _StubModerationService | None = None,
    tts: _StubTTSProvider | _FailingTTSProvider | None = None,
) -> tuple[_StubModerationService, _StubTTSProvider | _FailingTTSProvider]:
    mod = moderation or _StubModerationService()
    primary = tts or _StubTTSProvider()
    router = TTSRouter(primary=primary, first_byte_budget_s=2.0)  # type: ignore[arg-type]
    app.dependency_overrides[get_moderation_service] = lambda: mod  # type: ignore[arg-type,return-value]
    app.dependency_overrides[get_tts_router] = lambda: router
    return mod, primary


def _valid_body() -> dict[str, str]:
    return {"text": "下一句可以这样说：先问对方时间方便不方便。", "voice": "k-warm"}


# --------------------------------------------------------------------- #
# Auth + age gate (compliance, do not regress)                           #
# --------------------------------------------------------------------- #


async def test_no_token_returns_401(client: AsyncClient) -> None:
    _wire_dependencies()
    resp = await client.post("/v1/tts/synthesize", json=_valid_body())
    assert resp.status_code == 401
    assert resp.json()["code"] == "UNAUTHORIZED"


async def test_age_unset_returns_403_age_required(client: AsyncClient) -> None:
    _wire_dependencies()
    token = mint_token(
        user_id="u_no_age",
        persona_type="intern",
        is_minor=False,
        age_set=False,
    )

    resp = await client.post(
        "/v1/tts/synthesize",
        headers={"Authorization": f"Bearer {token}"},
        json=_valid_body(),
    )
    assert resp.status_code == 403
    assert resp.json()["code"] == "AGE_REQUIRED"


async def test_minor_returns_403_minor_forbidden(client: AsyncClient) -> None:
    """R-15: TTS exists today only as copilot's audio surface, which is
    adult-only. A minor JWT must never reach the synthesizer."""
    _wire_dependencies()
    token = mint_token(
        user_id="u_minor",
        persona_type="in_school",
        is_minor=True,
        age_set=True,
    )

    resp = await client.post(
        "/v1/tts/synthesize",
        headers={"Authorization": f"Bearer {token}"},
        json=_valid_body(),
    )
    assert resp.status_code == 403
    assert resp.json()["code"] == "MINOR_FORBIDDEN"


# --------------------------------------------------------------------- #
# Validation                                                             #
# --------------------------------------------------------------------- #


async def test_empty_text_returns_422(client: AsyncClient) -> None:
    _wire_dependencies()

    resp = await client.post(
        "/v1/tts/synthesize",
        headers={"Authorization": f"Bearer {_adult_token()}"},
        json={"text": "", "voice": "k-warm"},
    )
    assert resp.status_code == 422


async def test_overlong_text_returns_422(client: AsyncClient) -> None:
    """Cap at MAX_TEXT_CHARACTERS — pin the route refuses oversize
    before any vendor I/O."""
    from app.tts.provider import MAX_TEXT_CHARACTERS

    _wire_dependencies()
    resp = await client.post(
        "/v1/tts/synthesize",
        headers={"Authorization": f"Bearer {_adult_token()}"},
        json={"text": "x" * (MAX_TEXT_CHARACTERS + 1), "voice": "k-warm"},
    )
    assert resp.status_code == 422


async def test_unknown_voice_returns_422(client: AsyncClient) -> None:
    """`voice` is a Pydantic Literal — off-list values get 422."""
    _wire_dependencies()
    resp = await client.post(
        "/v1/tts/synthesize",
        headers={"Authorization": f"Bearer {_adult_token()}"},
        json={"text": "hi", "voice": "k-elf"},
    )
    assert resp.status_code == 422


# --------------------------------------------------------------------- #
# Moderation                                                             #
# --------------------------------------------------------------------- #


async def test_block_verdict_returns_422_tts_input_blocked(client: AsyncClient) -> None:
    _wire_dependencies(moderation=_StubModerationService(verdict="block"))

    resp = await client.post(
        "/v1/tts/synthesize",
        headers={"Authorization": f"Bearer {_adult_token()}"},
        json=_valid_body(),
    )
    assert resp.status_code == 422
    body = resp.json()
    assert body["code"] == "TTS_INPUT_BLOCKED"
    assert body["verdict"] == "block"


async def test_redirect_verdict_returns_422_tts_input_blocked(client: AsyncClient) -> None:
    """Red-line `redirect` (e.g. self-harm) must NOT reach the TTS vendor
    — the moderation service already handed back a crisis-resource
    pointer; speaking the original text would be the worst UX."""
    _wire_dependencies(moderation=_StubModerationService(verdict="redirect"))

    resp = await client.post(
        "/v1/tts/synthesize",
        headers={"Authorization": f"Bearer {_adult_token()}"},
        json=_valid_body(),
    )
    assert resp.status_code == 422
    assert resp.json()["code"] == "TTS_INPUT_BLOCKED"


async def test_moderation_backend_outage_returns_503(client: AsyncClient) -> None:
    """A moderation backend down doesn't fail open — we'd be speaking
    unscored content. 503 is the safer behaviour."""
    _wire_dependencies(
        moderation=_StubModerationService(
            raises=ModerationBackendError("moderation backend exploded", backend="aliyun")
        )
    )

    resp = await client.post(
        "/v1/tts/synthesize",
        headers={"Authorization": f"Bearer {_adult_token()}"},
        json=_valid_body(),
    )
    assert resp.status_code == 503
    assert resp.json()["code"] == "TTS_UNAVAILABLE"


async def test_moderation_is_called_with_jwt_user_id(client: AsyncClient) -> None:
    """The route MUST pass the JWT-derived user_id to the moderation
    audit row — never trust a body-provided id."""
    mod, _ = _wire_dependencies()

    resp = await client.post(
        "/v1/tts/synthesize",
        headers={"Authorization": f"Bearer {_adult_token()}"},
        json=_valid_body(),
    )
    assert resp.status_code == 200
    assert len(mod.calls) == 1
    assert mod.calls[0]["user_id"] == "u_adult"
    assert mod.calls[0]["is_minor"] is False


# --------------------------------------------------------------------- #
# Successful synthesis                                                   #
# --------------------------------------------------------------------- #


async def test_adult_allow_returns_audio_stream(client: AsyncClient) -> None:
    _, _ = _wire_dependencies(tts=_StubTTSProvider(audio=b"GREEN-AUDIO"))

    resp = await client.post(
        "/v1/tts/synthesize",
        headers={"Authorization": f"Bearer {_adult_token()}"},
        json=_valid_body(),
    )

    assert resp.status_code == 200
    assert resp.headers["content-type"] == "audio/mpeg"
    assert resp.content == b"GREEN-AUDIO"
    assert "X-Trace-Id" in resp.headers


async def test_audio_format_wav_sets_content_type(client: AsyncClient) -> None:
    """The route must derive the response content-type from the request
    `audio_format` — wrong content-type would make `<audio>` reject
    the body."""
    _wire_dependencies()

    resp = await client.post(
        "/v1/tts/synthesize",
        headers={"Authorization": f"Bearer {_adult_token()}"},
        json={"text": "hi", "voice": "k-warm", "audio_format": "wav"},
    )

    assert resp.status_code == 200
    assert resp.headers["content-type"] == "audio/wav"


async def test_warn_verdict_still_synthesizes(client: AsyncClient) -> None:
    """`warn` is not a block; the route must still synthesize so the
    coach can speak softened-but-allowed content."""
    _, _ = _wire_dependencies(
        moderation=_StubModerationService(verdict="warn"),
        tts=_StubTTSProvider(audio=b"WARN-AUDIO"),
    )

    resp = await client.post(
        "/v1/tts/synthesize",
        headers={"Authorization": f"Bearer {_adult_token()}"},
        json=_valid_body(),
    )

    assert resp.status_code == 200
    assert resp.content == b"WARN-AUDIO"


async def test_provider_failure_truncates_stream_to_empty_body(client: AsyncClient) -> None:
    """When the provider raises mid-stream (or before yielding anything),
    we can't change the already-sent 200 headers — but the body ends.
    Pin "no audio bytes" rather than a stack trace leaking into the
    response. The client sees an empty audio body + X-Trace-Id and
    consults the structured log for the failure detail."""
    _wire_dependencies(tts=_FailingTTSProvider())

    resp = await client.post(
        "/v1/tts/synthesize",
        headers={"Authorization": f"Bearer {_adult_token()}"},
        json=_valid_body(),
    )

    assert resp.status_code == 200
    assert resp.content == b""
    assert "X-Trace-Id" in resp.headers


# Mypy / lint silence — unused but kept for the test_*-module export
# narrative (the helper above types via `ModerationService` reflexion).
_ = ModerationService
