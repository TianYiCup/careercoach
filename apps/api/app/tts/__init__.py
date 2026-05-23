"""TTS (text-to-speech) provider abstraction layer.

Foundation §3.4.2 / PRD US-B2 — Coach K's earphone whisper for the
realtime copilot needs a server-side TTS path so the WS bridge can
ship synthesized audio alongside the existing `hint_delta` / `hint_done`
text events. The provider seam mirrors `app/asr/` and `app/llm/` so the
same DI + routing primitives carry over.

Public surface
--------------
  * `TTSProvider` Protocol + `TTSAudioChunk` envelope
  * `DummyTTSProvider` (in-process, synthetic WAV)
  * `EdgeTTSProvider` (Microsoft Edge TTS over WSS — primary)
  * `AliyunTTSProvider` (Aliyun NLS streaming TTS — backup)
  * `TTSError` hierarchy
  * `TTSRouter` failover-aware composite
  * `get_tts_provider()` / `get_tts_router()` DI factories

A1.1 shipped the abstraction + dummy backend; A1.2 shipped the
`POST /v1/tts/synthesize` endpoint; A1.3 (this PR) adds the vendor
adapters + expands the factory to wire the Edge primary → Aliyun backup
chain when both are configured.
"""

from app.tts.aliyun import AliyunTTSProvider
from app.tts.dummy import DummyTTSProvider
from app.tts.edge import EdgeTTSProvider
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
    "AliyunTTSProvider",
    "DummyTTSProvider",
    "EdgeTTSProvider",
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
