"""TTS (text-to-speech) provider abstraction layer — foundation PR.

Foundation §3.4.2 / PRD US-B2 — Coach K's earphone whisper for the
realtime copilot needs a server-side TTS path so the WS bridge can
ship synthesized audio alongside the existing `hint_delta` / `hint_done`
text events. The provider seam mirrors `app/asr/` and `app/llm/` so the
same DI + routing primitives carry over.

Public surface (A1.1)
---------------------
  * `TTSProvider` Protocol + `TTSAudioChunk` envelope
  * `DummyTTSProvider` (in-process, synthetic WAV)
  * `TTSError` hierarchy
  * `TTSRouter` failover-aware composite
  * `get_tts_provider()` / `get_tts_router()` DI factories (dummy only)

The Edge TTS + Aliyun TTS adapters land in the follow-up stacked PR
(A1.2). The Protocol shape and router/factory wiring are deliberately
positioned here so A1.2 is a pure add — no signature drift inside the
abstraction.
"""

from app.tts.dummy import DummyTTSProvider
from app.tts.errors import (
    TTSAuthError,
    TTSError,
    TTSMalformedResponseError,
    TTSTimeoutError,
    TTSUpstreamError,
)
from app.tts.factory import get_tts_provider, get_tts_router
from app.tts.provider import (
    DEFAULT_TIMEOUT_SECONDS,
    TTSAudioChunk,
    TTSAudioFormat,
    TTSProvider,
)
from app.tts.router import TTSRouter

__all__ = [
    "DEFAULT_TIMEOUT_SECONDS",
    "DummyTTSProvider",
    "TTSAudioChunk",
    "TTSAudioFormat",
    "TTSAuthError",
    "TTSError",
    "TTSMalformedResponseError",
    "TTSProvider",
    "TTSRouter",
    "TTSTimeoutError",
    "TTSUpstreamError",
    "get_tts_provider",
    "get_tts_router",
]
