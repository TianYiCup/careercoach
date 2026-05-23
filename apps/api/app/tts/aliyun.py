"""Aliyun NLS streaming TTS adapter — backup provider.

The wire protocol mirrors `app.asr.aliyun` (NLS realtime ASR): same
authentication (account AK + per-project AppKey), same WebSocket
endpoint family (`wss://nls-gateway-…/ws/v1`), same JSON control-frame
shape (`SynthesisStart` / audio chunks / `SynthesisCompleted` instead
of `TranscriptionStarted` / `TranscriptionResultChanged` / `Completed`).

v1 ships only the construction + auth wiring; the actual `synthesize`
implementation can be filled in when the team is ready to enable a
paid backup. The skeleton holds the seam open so the `TTSRouter` chain
already advertises a backup slot, and a future PR is one method
override rather than a new file + new tests + new factory branch.

Why ship the skeleton now
-------------------------
Constraint #4 (LLM/ASR/TTS must support primary→backup failover) wants
the router to *exist* and route correctly the moment Edge TTS goes
down. Wiring the skeleton means the router-level tests today exercise
the multi-provider failover code path — even if the failover lands on
a "not yet implemented" leaf. When the real Aliyun impl arrives, the
router behavior doesn't change, only what the leaf does.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

import structlog

from app.tts.errors import TTSUpstreamError
from app.tts.provider import (
    DEFAULT_AUDIO_FORMAT,
    DEFAULT_TIMEOUT_SECONDS,
    DEFAULT_VOICE,
    TTSAudioChunk,
    TTSAudioFormat,
    TTSVoice,
)

logger = structlog.get_logger(__name__)

PROVIDER_NAME = "aliyun"

# Voice catalog mapping the stable API-side voice id to the Aliyun NLS
# voice name. `xiaoyun` is Aliyun's warm Mandarin female — the closest
# match to the Edge `Xiaoxiao` voice so a primary→backup failover
# doesn't change the perceived speaker mid-session.
_VOICE_CATALOG: dict[TTSVoice, str] = {
    "k-warm": "xiaoyun",
}


class AliyunTTSProvider:
    """Streaming TTS client for Aliyun NLS realtime.

    Structurally implements `app.tts.provider.TTSProvider`. v1 stub
    raises `TTSUpstreamError` so the router's failover code path
    treats this provider as "configured but unavailable" — exactly the
    behaviour the production swap-in needs to preserve when it
    replaces this with the real impl.
    """

    name = PROVIDER_NAME

    def __init__(
        self,
        *,
        access_key_id: str,
        access_key_secret: str,
        app_key: str,
        ws_url: str,
        token_url: str,
    ) -> None:
        self._access_key_id = access_key_id
        self._access_key_secret = access_key_secret
        self._app_key = app_key
        self._ws_url = ws_url
        self._token_url = token_url

    async def synthesize(
        self,
        text: str,
        *,
        voice: TTSVoice = DEFAULT_VOICE,
        audio_format: TTSAudioFormat = DEFAULT_AUDIO_FORMAT,
        timeout: float = DEFAULT_TIMEOUT_SECONDS,
    ) -> AsyncIterator[TTSAudioChunk]:
        vendor_voice = _VOICE_CATALOG.get(voice)
        logger.info(
            "tts_aliyun_synthesize_unimplemented",
            voice=voice,
            vendor_voice=vendor_voice,
            audio_format=audio_format,
            char_count=len(text),
        )
        # `yield` keeps mypy + the AsyncIterator protocol happy without
        # producing audio; the immediate raise after the empty yield
        # makes the body a generator that always errors before emitting
        # a chunk. Tests assert on the `TTSUpstreamError` type.
        if False:  # pragma: no cover — generator priming only
            yield TTSAudioChunk(audio=b"", is_final=True)
        raise TTSUpstreamError(
            "aliyun tts not implemented yet — backup slot reserved",
            provider=self.name,
        )


__all__ = ["AliyunTTSProvider"]
