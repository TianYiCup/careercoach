"""FastAPI entry point.

Exposes /health and (later) the versioned /v1/* business surface.
Sentry is initialized only when SENTRY_DSN is set so dev runs stay quiet.
"""

import logging

import sentry_sdk
import structlog
from fastapi import FastAPI
from sentry_sdk.integrations.asgi import SentryAsgiMiddleware

from app import __version__
from app.config import get_settings
from app.routes.health import router as health_router

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

    app.include_router(health_router)

    logger.info(
        "app_started",
        version=__version__,
        env=settings.app_env,
        sentry=bool(settings.sentry_dsn),
    )

    return app


app = create_app()
