"""Liveness + readiness probes.

`/health` is a cheap liveness check — returns 200 if the process is alive.
`/health/ready` will (in a later commit) verify Postgres + Redis + Qdrant.
"""

from fastapi import APIRouter
from pydantic import BaseModel

from app import __version__
from app.config import get_settings

router = APIRouter(tags=["health"])


class HealthResponse(BaseModel):
    status: str
    version: str
    env: str


@router.get("/health", response_model=HealthResponse)
async def health() -> HealthResponse:
    settings = get_settings()
    return HealthResponse(status="ok", version=__version__, env=settings.app_env)
