"""Liveness + readiness probes.

`/health` is a cheap liveness check — returns 200 if the process is
alive. Use this for k8s livenessProbe / load-balancer health checks
where a 200 means "don't kill me".

`/health/ready` (A-35) is the readiness probe — returns 200 only
when all critical external dependencies are reachable. Use this for
k8s readinessProbe / deployment platform "is this instance OK to
receive traffic" gates. A failed readiness check should remove the
instance from the LB pool without killing the process — transient
outages auto-recover when the dep comes back.

Dependency matrix (A-35 + A-36)

  llm_router  — primary LLM provider has credentials (no network)
  asr         — Aliyun NLS token cache exercise (skipped on dummy)
  moderation  — backend type inspection (no network)
  db_postgres — `SELECT 1` against the async engine (skipped when
                no repo backend is configured to use postgres)  [A-36]
  db_redis    — `PING` against the configured redis URL (skipped
                when no backend is configured to use redis)     [A-36]

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
               in dev, or every repo backend is `memory`); doesn't
               count as failure
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
from redis.asyncio import Redis
from redis.exceptions import RedisError
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncEngine

from app import __version__
from app.asr import ASRError, ASRProvider, get_asr_provider
from app.asr.aliyun import AliyunASRProvider
from app.config import Settings, get_settings
from app.db.session import engine as _module_engine
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
        _check_postgres(settings),
        _check_redis(settings),
    )
    checks = {
        "llm_router": check_results[0],
        "asr": check_results[1],
        "moderation": check_results[2],
        "db_postgres": check_results[3],
        "db_redis": check_results[4],
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


async def _check_postgres(settings: Settings) -> ReadinessCheck:
    """Postgres readiness (A-36).

    Skips entirely when every repo backend is `memory` — Postgres isn't
    actually a dependency in that config, so a missing DB shouldn't
    flip the instance to `degraded`. When ANY repo backend is wired
    to `postgres`, runs a `SELECT 1` against the async engine with a
    2s timeout.

    `engine.connect()` re-uses the pool (no per-probe TCP handshake
    once warm), so steady-state cost is effectively a single round-
    trip to the DB. First hit after deploy pays the pool-warm latency
    which is exactly what readiness is for.
    """
    start = time.monotonic()
    consumers = _postgres_consumers(settings)
    if not consumers:
        return _build_check(
            "skipped",
            start,
            detail="no backend uses postgres (all repos = memory)",
        )
    engine = _get_engine()
    try:
        async with asyncio.timeout(_READINESS_CHECK_TIMEOUT_S):
            async with engine.connect() as conn:
                await conn.execute(text("SELECT 1"))
    except TimeoutError:
        return _build_check(
            "fail",
            start,
            detail=f"postgres SELECT 1 exceeded {_READINESS_CHECK_TIMEOUT_S}s",
        )
    except SQLAlchemyError as exc:
        return _build_check(
            "fail",
            start,
            detail=f"{type(exc).__name__}: {exc}",
        )
    return _build_check(
        "ok",
        start,
        detail=f"postgres OK (consumers={','.join(consumers)})",
    )


async def _check_redis(settings: Settings) -> ReadinessCheck:
    """Redis readiness (A-36).

    Skips when no backend is configured to use Redis (v0: only the
    SMS code store). When wired, opens a transient client via
    `Redis.from_url` and runs `PING` under a 2s timeout. The client
    is closed in `finally` so a failure doesn't leak a connection
    into the pool.

    A transient client is the right choice here — Redis.from_url is
    cheap (lazy-connecting) and the readiness check shouldn't share
    a connection with the request-path limiter / code-store: a stuck
    probe shouldn't be able to starve real traffic.
    """
    start = time.monotonic()
    consumers = _redis_consumers(settings)
    if not consumers:
        return _build_check(
            "skipped",
            start,
            detail="no backend uses redis",
        )
    client = _make_redis_client(settings.redis_url)
    try:
        async with asyncio.timeout(_READINESS_CHECK_TIMEOUT_S):
            await client.ping()
    except TimeoutError:
        return _build_check(
            "fail",
            start,
            detail=f"redis PING exceeded {_READINESS_CHECK_TIMEOUT_S}s",
        )
    except RedisError as exc:
        return _build_check(
            "fail",
            start,
            detail=f"{type(exc).__name__}: {exc}",
        )
    finally:
        # `aclose` is the redis-py 5.x replacement for `close`; safe
        # to call even when the client never connected (lazy).
        await client.aclose()
    return _build_check(
        "ok",
        start,
        detail=f"redis OK (consumers={','.join(consumers)})",
    )


def _postgres_consumers(settings: Settings) -> list[str]:
    """Names of backends currently wired to `postgres`.

    Surfaced in the `ok` detail so an analyst can see which features
    a Postgres outage would actually impact (vs `memory`, which keeps
    working through the outage).
    """
    mapping = {
        "sessions": settings.sessions_repo_backend,
        "review": settings.review_repo_backend,
        "copilot": settings.copilot_repo_backend,
        "auth": settings.auth_repo_backend,
        "sharecards_score": settings.sharecards_score_repo_backend,
    }
    return [name for name, backend in mapping.items() if backend == "postgres"]


def _redis_consumers(settings: Settings) -> list[str]:
    """Names of backends currently wired to `redis`.

    v0 has one consumer (the SMS code store / rate limiter pair, which
    share a backend choice). Listed here so the matrix is greppable
    when more land — every redis-using backend MUST register here so
    its outage flips readiness.
    """
    if settings.auth_code_store_backend == "redis":
        return ["auth_code_store"]
    return []


def _get_engine() -> AsyncEngine:
    """Indirection so tests can monkeypatch this without poking at
    the module-level engine attribute."""
    return _module_engine


def _make_redis_client(url: str) -> Redis:
    """Indirection so tests can monkeypatch this without patching
    `Redis.from_url` globally (which would affect unrelated callers
    sharing the same module import)."""
    # redis-py's `from_url` stubs return `Any`; narrowing here so the
    # contract surfaces a typed Redis to callers.
    return Redis.from_url(url)  # type: ignore[no-any-return]


def _build_check(
    status: CheckStatus,
    start_monotonic: float,
    *,
    detail: str | None = None,
) -> ReadinessCheck:
    elapsed_ms = int((time.monotonic() - start_monotonic) * 1000)
    return ReadinessCheck(status=status, latency_ms=elapsed_ms, detail=detail)
