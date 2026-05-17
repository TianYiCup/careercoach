"""ASR DI factory.

Backends:
  * `dummy`  — in-process UTF-8 echo (A-17). Default; covers tests +
              dev runs without an ASR vendor account.
  * `aliyun` — Aliyun NLS realtime streaming (A-28). Requires
              ALIYUN_ACCESS_KEY_ID/SECRET (shared with moderation)
              plus ALIYUN_ASR_APP_KEY (per-project NLS key).

Callers never see the concrete adapter — they depend on the
`ASRProvider` Protocol and the factory hands them whichever one
settings selected.

The `@lru_cache` makes the provider process-wide. Tests override via
`app.dependency_overrides[get_asr_provider] = ...` or by calling
`get_asr_provider.cache_clear()` before mutating settings.
"""

from __future__ import annotations

from functools import lru_cache

import structlog

from app.asr.aliyun import AliyunASRProvider
from app.asr.dummy import DummyASRProvider
from app.asr.provider import ASRProvider
from app.config import get_settings

logger = structlog.get_logger(__name__)


@lru_cache(maxsize=1)
def get_asr_provider() -> ASRProvider:
    """Process-wide ASR provider singleton.

    The `Literal` type on the settings field constrains the value, so
    only the branches below need to be live. The trailing `raise`
    catches a widened Literal that forgot to wire a new adapter.
    """
    settings = get_settings()
    backend = settings.asr_backend
    if backend == "dummy":
        logger.info("asr_provider_wired", backend="dummy")
        return DummyASRProvider()
    if backend == "aliyun":
        ak_id = settings.aliyun_access_key_id.get_secret_value()
        ak_secret = settings.aliyun_access_key_secret.get_secret_value()
        app_key = settings.aliyun_asr_app_key.get_secret_value()
        # Refuse to construct rather than fall back to dummy — silent
        # downgrade would let a misconfigured prod deploy "work" but
        # produce zero transcription, which is the worst failure mode.
        if not ak_id or not ak_secret:
            raise ValueError(
                "asr_backend=aliyun requires ALIYUN_ACCESS_KEY_ID + ALIYUN_ACCESS_KEY_SECRET"
            )
        if not app_key:
            raise ValueError("asr_backend=aliyun requires ALIYUN_ASR_APP_KEY")
        logger.info(
            "asr_provider_wired",
            backend="aliyun",
            ws_url=settings.aliyun_asr_ws_url,
        )
        return AliyunASRProvider(
            access_key_id=ak_id,
            access_key_secret=ak_secret,
            app_key=app_key,
            ws_url=settings.aliyun_asr_ws_url,
            token_url=settings.aliyun_asr_token_url,
        )
    raise ValueError(f"unsupported asr backend: {backend!r}")
