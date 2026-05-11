"""FastAPI entry point.

Exposes /health and the versioned /v1/* business surface.
Sentry is initialized only when SENTRY_DSN is set so dev runs stay quiet.
"""

import logging
import uuid

import sentry_sdk
import structlog
from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from sentry_sdk.integrations.asgi import SentryAsgiMiddleware

from app import __version__
from app.config import get_settings
from app.routes.health import router as health_router
from app.routes.v1 import router as v1_router

logger = structlog.get_logger(__name__)


def _configure_logging(level: str) -> None:
    logging.basicConfig(level=level.upper(), format="%(message)s")
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso", utc=True),
            structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(getattr(logging, level.upper())),
        cache_logger_on_first_use=True,
    )


def create_app() -> FastAPI:
    settings = get_settings()
    _configure_logging(settings.app_log_level)

    if settings.sentry_dsn:
        sentry_sdk.init(
            dsn=settings.sentry_dsn,
            environment=settings.app_env,
            traces_sample_rate=0.1 if settings.app_env == "production" else 1.0,
        )

    app = FastAPI(
        title="CareerCoach AI",
        description="中文语境对话练习教练 · 后端 API",
        version=__version__,
        docs_url="/docs",
        redoc_url="/redoc",
        openapi_url="/openapi.json",
    )

    if settings.sentry_dsn:
        app.add_middleware(SentryAsgiMiddleware)

    _register_error_handlers(app)

    app.include_router(health_router)
    app.include_router(v1_router)

    logger.info(
        "app_started",
        version=__version__,
        env=settings.app_env,
        sentry=bool(settings.sentry_dsn),
    )

    return app


def _trace_id(request: Request) -> str:
    return request.headers.get("x-request-id") or str(uuid.uuid4())


def _register_error_handlers(app: FastAPI) -> None:
    """Convert HTTPException + validation errors to the PRD §7.1 envelope."""

    @app.exception_handler(HTTPException)
    async def _http_exception(request: Request, exc: HTTPException) -> JSONResponse:
        if isinstance(exc.detail, dict) and "code" in exc.detail and "message" in exc.detail:
            payload = {**exc.detail, "trace_id": _trace_id(request)}
        else:
            payload = {
                "code": _default_code_for(exc.status_code),
                "message": str(exc.detail) if exc.detail is not None else "request failed",
                "trace_id": _trace_id(request),
            }
        return JSONResponse(status_code=exc.status_code, content=payload)

    @app.exception_handler(RequestValidationError)
    async def _validation_error(request: Request, exc: RequestValidationError) -> JSONResponse:
        return JSONResponse(
            status_code=422,
            content={
                "code": "VALIDATION_ERROR",
                "message": "request body or query failed schema validation",
                "trace_id": _trace_id(request),
                "errors": exc.errors(),
            },
        )


def _default_code_for(status_code: int) -> str:
    return {
        400: "BAD_REQUEST",
        401: "UNAUTHORIZED",
        403: "FORBIDDEN",
        404: "NOT_FOUND",
        429: "RATE_LIMIT_EXCEEDED",
        500: "INTERNAL_ERROR",
        501: "NOT_IMPLEMENTED",
    }.get(status_code, "ERROR")


app = create_app()
