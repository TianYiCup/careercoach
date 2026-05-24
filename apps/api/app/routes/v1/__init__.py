"""v1 HTTP surface — mounted at /v1 in app.main.

Stubs for sprint-0-task-split §2.1 endpoints. Each handler validates input via
its Pydantic model (so Schema is honored) and then raises 501 NotImplemented
with the standard error envelope.
"""

from fastapi import APIRouter

from app.routes.v1 import (
    auth,
    copilot,
    mascot,
    moderation,
    ops,
    personas,
    review,
    scenarios,
    sessions,
    sharecards,
    streak,
    tts,
    users,
    vibe,
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
# A-42: ops-only surface (X-Ops-Token gated). First endpoint is
# /v1/ops/token-cost for per-user LLM spend rollups.
router.include_router(ops.router)
# R3-1: daily mood check-in. Plain JWT auth — no age gate (no LLM path).
router.include_router(vibe.router)
# R3-2: practice-streak counter. Read-only here; advanced by POST /sessions.
router.include_router(streak.router)
# US-A2: opponent persona catalog. Static, read-only, no auth (like /scenarios).
router.include_router(personas.router)
# US-B2: streaming TTS for Coach K's earphone whisper. Adult-only via
# require_adult (same compliance gate as copilot).
router.include_router(tts.router)
# §7.10: 教练 K expression timeline. Plain JWT auth — no LLM path.
router.include_router(mascot.router)

__all__ = ["router"]
