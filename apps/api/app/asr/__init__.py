"""ASR (automatic speech recognition) provider abstraction layer.

Foundation §3.3 reserves an ASR seam parallel to the LLM seam. A-17
ships the Protocol + a dummy adapter + a settings-driven factory; a
later PR wires it into the copilot WS handler and adds real vendor
adapters (Aliyun NLS / Tencent ASR).

Public surface
--------------
  * `ASRProvider` Protocol + `ASREvent` envelope
  * `DummyASRProvider` (in-process, UTF-8 echo)
  * `ASRError` hierarchy (timeout / auth / upstream)
  * `get_asr_provider()` DI factory

The `app/asr/` package mirrors `app/llm/` deliberately so anyone who
has read one knows where to look in the other.
"""

from app.asr.dummy import DummyASRProvider
from app.asr.errors import (
    ASRAuthError,
    ASRError,
    ASRTimeoutError,
    ASRUpstreamError,
)
from app.asr.factory import get_asr_provider
from app.asr.provider import (
    DEFAULT_TIMEOUT_SECONDS,
    ASREvent,
    ASREventKind,
    ASRProvider,
)

__all__ = [
    "DEFAULT_TIMEOUT_SECONDS",
    "ASRAuthError",
    "ASRError",
    "ASREvent",
    "ASREventKind",
    "ASRProvider",
    "ASRTimeoutError",
    "ASRUpstreamError",
    "DummyASRProvider",
    "get_asr_provider",
]
