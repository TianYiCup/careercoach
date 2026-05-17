"""Liveness + readiness probes.

`/health` is a cheap liveness check — returns 200 if the process is
alive. Use this for k8s livenessProbe / load-balancer health checks
where a 200 means "don't kill me".

`/health/ready` (A-35) is the readiness probe — returns 200 only
when all critical external dependencies (LLM router, ASR, moderation)
are reachable. Use this for k8s readinessProbe / deployment platform
"is this instance OK to receive traffic" gates. A failed readiness
check should remove the instance from the LB pool without killing
the process — transient outages auto-recover when the dep comes back.

Why the split

  Liveness asks "is the process responsive?". Readiness asks "should
  traffic actually hit it?". A process can be alive but not ready
  (e.g. its upstream is down, so all requests would 5xx). Conflating
  them causes deployment platforms to RESTART the process on every
  upstream blip — exactly what readiness was designed to avoid.

Check semantics

  Each dependency check returns a `ReadinessCheck` with a status:
    ok       — dep responded as expected within budget
    skipped  — dep not configured in this env (e.g. asr_backend=dummy
               in dev); doesn't count as failure
    fail     — dep didn't respond, returned an error, or timed out

  Top-level status:
    ready    — every check is `ok` or `skipped`  → HTTP 200
    degraded — at least one check is `fail`      → HTTP 503

  Latency is captured per-check so an ops dashboard can graph the
  trend (a 300ms moderation probe is a leading indicator of a soon-
  to-fail-with-timeout call path).
"""

from __future__ import annotations

import asyncio
import time
from typing import Literal

import structlog
from fastapi import APIRouter, Depends, Response
from pydantic import BaseModel, Field

from app import __version__
from app.asr import ASRError, ASRProvider, get_asr_provider
from app.asr.aliyun import AliyunASRProvider
from app.config import Settings, get_settings
from app.llm.factory import get_llm_router
from app.llm.router import LLMRouter
from app.services.moderation import (
    CascadingBackend,
    ModerationService,
    get_moderation_service,
)

logger = structlog.get_logger(__name__)

router = APIRouter(tags=["health"])

# Per-check budget for the network-touching probes (ASR token fetch
# in particular). Kept tight on purpose — readiness probes typically
# fire every few seconds, so a long timeout would let one slow check
# keep a degraded instance "ready" for too long.
_READINESS_CHECK_TIMEOUT_S = 2.0

CheckStatus = Literal["ok", "skipped", "fail"]
ReadinessStatus = Literal["ready", "degraded"]


class HealthResponse(BaseModel):
    status: str
    version: str
    env: str


class ReadinessCheck(BaseModel):
    """One dependency's readiness state."""

    status: CheckStatus
    latency_ms: int = Field(
        ...,
        description="Wall-clock duration of the check, including network.",
    )
    detail: str | None = Field(
        default=None,
        description=(
            "Free-form context — for `fail` this is the error class; for "
            "`skipped` the reason (e.g. `asr_backend=dummy`); for `ok` "
            "the resolved backend type when there's more than one option."
        ),
    )


class ReadinessResponse(BaseModel):
    """Aggregated readiness payload returned by `/health/ready`."""

    status: ReadinessStatus
    version: str
    env: str
    checks: dict[str, ReadinessCheck]


@router.get("/health", response_model=HealthResponse)
async def health() -> HealthResponse:
    settings = get_settings()
    return HealthResponse(status="ok", version=__version__, env=settings.app_env)


@router.get(
    "/health/ready",
    response_model=ReadinessResponse,
    responses={
        200: {"description": "All critical dependencies reachable."},
        503: {
            "description": (
                "At least one critical dependency failed its probe. "
                "Body still returns the per-check breakdown so ops can "
                "see which dep is the problem without scraping logs."
            )
        },
    },
)
async def ready(
    response: Response,
    llm_router: LLMRouter = Depends(get_llm_router),
    asr_provider: ASRProvider = Depends(get_asr_provider),
    moderation_service: ModerationService = Depends(get_moderation_service),
    settings: Settings = Depends(get_settings),
) -> ReadinessResponse:
    """Run all dependency probes in parallel and aggregate.

    `asyncio.gather` is the right primitive here — a slow check
    shouldn't serialize behind a fast one, and each check has its
    own timeout so the worst-case latency is bounded by
    `_READINESS_CHECK_TIMEOUT_S` (not the sum).
    """
    check_results = await asyncio.gather(
        _check_llm(llm_router),
        _check_asr(asr_provider, settings),
        _check_moderation(moderation_service),
    )
    checks = {
        "llm_router": check_results[0],
        "asr": check_results[1],
        "moderation": check_results[2],
    }
    overall: ReadinessStatus = (
        "degraded" if any(c.status == "fail" for c in checks.values()) else "ready"
    )
    if overall == "degraded":
        response.status_code = 503
        logger.warning(
            "health_ready_degraded",
            failed=[name for name, c in checks.items() if c.status == "fail"],
        )
    return ReadinessResponse(
        status=overall,
        version=__version__,
        env=settings.app_env,
        checks=checks,
    )


async def _check_llm(router: LLMRouter) -> ReadinessCheck:
    """LLM router readiness.

    No network call — we just verify the chain has at least one
    real provider. A `NoCredentialsProvider` in the primary slot
    means boot succeeded but every `/turns` call will 5xx; readiness
    must surface that as a failure so the LB drops the instance.
    """
    start = time.monotonic()
    # Avoid an isinstance import dance with the private symbol; the
    # `name` attribute is part of the Protocol contract and stable.
    primary_name = router._chain[0].name  # probe-only access
    if primary_name == "no_credentials":
        return _build_check(
            "fail",
            start,
            detail="no LLM credentials configured (DEEPSEEK_API_KEY / QWEN_API_KEY)",
        )
    backup_names = [p.name for p in router._chain[1:]]  # probe-only access
    return _build_check(
        "ok",
        start,
        detail=f"primary={primary_name}, backups={backup_names}",
    )


async def _check_asr(provider: ASRProvider, settings: Settings) -> ReadinessCheck:
    """ASR readiness.

    `dummy` backend skips the network check — dev/test mode is a
    valid configuration, not a deploy failure. `aliyun` exercises
    the token cache: post-first-fetch this is a no-op; first hit
    pays one network round-trip which is exactly what we want a
    readiness probe to measure.
    """
    start = time.monotonic()
    if settings.asr_backend == "dummy":
        return _build_check("skipped", start, detail="asr_backend=dummy")
    if not isinstance(provider, AliyunASRProvider):
        # Defensive: a future Literal widening with no readiness
        # support shouldn't silently pass. Report skipped + the
        # actual class so an analyst sees what's wired.
        return _build_check(
            "skipped",
            start,
            detail=f"unrecognised provider type: {type(provider).__name__}",
        )
    try:
        async with asyncio.timeout(_READINESS_CHECK_TIMEOUT_S):
            # Access the cache directly; it's the cheap path that
            # short-circuits if the cached token still has life.
            await provider._token_cache.get(  # probe-only access
                access_key_id=settings.aliyun_access_key_id.get_secret_value(),
                access_key_secret=settings.aliyun_access_key_secret.get_secret_value(),
                endpoint_url=settings.aliyun_asr_token_url,
                timeout_s=_READINESS_CHECK_TIMEOUT_S,
            )
    except TimeoutError:
        return _build_check(
            "fail",
            start,
            detail=f"aliyun token fetch exceeded {_READINESS_CHECK_TIMEOUT_S}s",
        )
    except ASRError as exc:
        return _build_check("fail", start, detail=f"{type(exc).__name__}: {exc}")
    return _build_check("ok", start, detail="aliyun (token fresh)")


async def _check_moderation(service: ModerationService) -> ReadinessCheck:
    """Moderation backend readiness.

    No network call — we report what's wired (cascading w/ cloud or
    local-dict only). Cloud probe via an actual mod call would cost
    money per readiness tick; the cascading backend already self-
    heals on cloud failure (falls through to local), so a missing
    cloud isn't a readiness failure — it's a degraded-mode signal
    captured in the detail string.
    """
    start = time.monotonic()
    backend = service._backend  # probe-only access
    backend_name = type(backend).__name__
    if isinstance(backend, CascadingBackend):
        return _build_check("ok", start, detail=f"{backend_name} (cloud + local)")
    return _build_check("ok", start, detail=f"{backend_name} (local-only)")


def _build_check(
    status: CheckStatus,
    start_monotonic: float,
    *,
    detail: str | None = None,
) -> ReadinessCheck:
    elapsed_ms = int((time.monotonic() - start_monotonic) * 1000)
    return ReadinessCheck(status=status, latency_ms=elapsed_ms, detail=detail)
