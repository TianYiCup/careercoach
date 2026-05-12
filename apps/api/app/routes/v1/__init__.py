"""v1 HTTP surface — mounted at /v1 in app.main.

Stubs for sprint-0-task-split §2.1 endpoints. Each handler validates input via
its Pydantic model (so Schema is honored) and then raises 501 NotImplemented
with the standard error envelope.
"""

from fastapi import APIRouter

from app.routes.v1 import auth, moderation, scenarios, sessions, sharecards

router = APIRouter(prefix="/v1")
router.include_router(auth.router)
router.include_router(scenarios.router)
router.include_router(sessions.router)
router.include_router(moderation.router)
router.include_router(sharecards.router)

__all__ = ["router"]
