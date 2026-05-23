"""TTS DI factory + router wiring.

Two surfaces:

* `get_tts_provider()` — return the configured primary provider in
  isolation, used by tests + the route that doesn't need failover.
* `get_tts_router()`   — return the failover-aware `TTSRouter` wrapping
  the configured chain. This is what the route handler depends on.

Backends
--------
  * `dummy`  — in-process synthetic WAV (default; covers tests + dev
               runs without a TTS vendor)
  * `edge`   — Microsoft Edge TTS over WSS (free, no key)
  * `aliyun` — Aliyun NLS streaming TTS (requires AK/AppKey)

When `tts_backend=edge` the router additionally wires Aliyun as a
backup IF its credentials are present — keeping the primary→backup
ladder live by default. With no Aliyun keys the chain collapses to
edge only (logged so ops sees the degradation explicitly).

The `@lru_cache` makes the provider process-wide. Tests override via
`app.dependency_overrides[get_tts_*] = …` or by calling
`get_tts_provider.cache_clear()` before mutating settings.
"""

from __future__ import annotations

from functools import lru_cache

import structlog

from app.config import Settings, get_settings
from app.tts.aliyun import AliyunTTSProvider
from app.tts.dummy import DummyTTSProvider
from app.tts.edge import EdgeTTSProvider
from app.tts.provider import TTSProvider
from app.tts.router import TTSRouter

logger = structlog.get_logger(__name__)


@lru_cache(maxsize=1)
def get_tts_provider() -> TTSProvider:
    """Process-wide primary TTS provider singleton (no failover).

    Used by tests and any caller that wants the single configured
    backend without the router's first-byte semantics.
    """
    settings = get_settings()
    backend = settings.tts_backend
    if backend == "dummy":
        logger.info("tts_provider_wired", backend="dummy")
        return DummyTTSProvider()
    if backend == "edge":
        logger.info("tts_provider_wired", backend="edge")
        return EdgeTTSProvider()
    if backend == "aliyun":
        provider = _build_aliyun_provider_or_raise(settings)
        logger.info("tts_provider_wired", backend="aliyun")
        return provider
    raise ValueError(f"unsupported tts backend: {backend!r}")


@lru_cache(maxsize=1)
def get_tts_router() -> TTSRouter:
    """Process-wide failover-aware TTS router singleton.

    Chain composition:
      * `tts_backend=dummy`  — dummy only (no failover possible)
      * `tts_backend=edge`   — edge primary; aliyun appended as backup
                                iff Aliyun credentials are configured
      * `tts_backend=aliyun` — aliyun only (degenerate chain; same
                                acceptance semantics so callers don't
                                need a per-backend code path)
    """
    settings = get_settings()
    backend = settings.tts_backend

    if backend == "dummy":
        logger.info("tts_router_wired", backend="dummy", backups=())
        return TTSRouter(primary=DummyTTSProvider())

    if backend == "edge":
        primary: TTSProvider = EdgeTTSProvider()
        backups: list[TTSProvider] = []
        if _aliyun_credentials_present(settings):
            backups.append(_build_aliyun_provider_or_raise(settings))
            logger.info("tts_router_wired", backend="edge", backups=("aliyun",))
        else:
            logger.info(
                "tts_router_wired",
                backend="edge",
                backups=(),
                note="aliyun backup skipped — credentials not configured",
            )
        return TTSRouter(primary=primary, backups=backups)

    if backend == "aliyun":
        logger.info("tts_router_wired", backend="aliyun", backups=())
        return TTSRouter(primary=_build_aliyun_provider_or_raise(settings))

    raise ValueError(f"unsupported tts backend: {backend!r}")


def _aliyun_credentials_present(settings: Settings) -> bool:
    """True iff the Aliyun TTS backup can be constructed without raising.

    Same set of secrets the ASR adapter expects, plus the per-project
    TTS AppKey (separate from the ASR AppKey on Aliyun's console).
    """
    ak_id = settings.aliyun_access_key_id.get_secret_value()
    ak_secret = settings.aliyun_access_key_secret.get_secret_value()
    app_key = settings.aliyun_tts_app_key.get_secret_value()
    return bool(ak_id and ak_secret and app_key)


def _build_aliyun_provider_or_raise(settings: Settings) -> AliyunTTSProvider:
    """Construct the Aliyun TTS leaf or raise loudly.

    Mirrors `app.asr.factory._build_aliyun_provider_or_raise` — refuse
    to silently degrade when production has flipped the backend to
    aliyun but forgotten to set keys. The router only calls this when
    credentials are present, so the raise is reachable solely from
    `get_tts_provider(backend="aliyun")` with missing keys.
    """
    ak_id = settings.aliyun_access_key_id.get_secret_value()
    ak_secret = settings.aliyun_access_key_secret.get_secret_value()
    app_key = settings.aliyun_tts_app_key.get_secret_value()
    if not ak_id or not ak_secret:
        raise ValueError(
            "tts_backend=aliyun requires ALIYUN_ACCESS_KEY_ID + ALIYUN_ACCESS_KEY_SECRET"
        )
    if not app_key:
        raise ValueError("tts_backend=aliyun requires ALIYUN_TTS_APP_KEY")
    return AliyunTTSProvider(
        access_key_id=ak_id,
        access_key_secret=ak_secret,
        app_key=app_key,
        ws_url=settings.aliyun_tts_ws_url,
        token_url=settings.aliyun_asr_token_url,
    )
