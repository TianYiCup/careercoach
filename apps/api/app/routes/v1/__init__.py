"""v1 HTTP surface — mounted at /v1 in app.main.

Stubs for sprint-0-task-split §2.1 endpoints. Each handler validates input via
its Pydantic model (so Schema is honored) and then raises 501 NotImplemented
with the standard error envelope.
"""

from fastapi import APIRouter

from app.routes.v1 import (
    auth,
    copilot,
    moderation,
    review,
    scenarios,
    sessions,
    sharecards,
    users,
)

router = APIRouter(prefix="/v1")
router.include_router(auth.router)
router.include_router(users.router)
router.include_router(scenarios.router)
router.include_router(sessions.router)
router.include_router(moderation.router)
router.include_router(sharecards.router)
# Stubs — wired now so the compliance gates (`require_adult` for copilot,
# `require_age_set` for review) fire from day one. Both raise 501 until
# the business logic lands. See `docs/b-side-review-2026-05-15/`.
router.include_router(copilot.router)
router.include_router(review.router)

__all__ = ["router"]
