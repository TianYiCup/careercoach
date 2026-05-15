"""`ModerationService` — orchestrates one moderation request.

End-to-end flow for `POST /v1/moderation/check`:

    request → service.check(request, trace_id)
            → backend.evaluate(content, context)        # PR ②/③
            → event_sink.record(request, decision, …)   # audit
            → ModerationCheckResponse

The service is the only place that knows about *all four* concerns
(public schema, backend, audit log, trace id). Keeping that here means
backends stay pure (no DB, no HTTP) and routes stay thin (no business
logic).
"""

from __future__ import annotations

from dataclasses import replace

import structlog

from app.schemas.moderation import ModerationCheckRequest, ModerationCheckResponse
from app.services.moderation.backend import ModerationBackend
from app.services.moderation.event_sink import ModerationEventSink
from app.services.moderation.types import Decision

logger = structlog.get_logger(__name__)


class ModerationService:
    def __init__(
        self,
        *,
        backend: ModerationBackend,
        event_sink: ModerationEventSink,
    ) -> None:
        self._backend = backend
        self._event_sink = event_sink

    async def check(
        self,
        request: ModerationCheckRequest,
        *,
        user_id: str,
        is_minor: bool = False,
        trace_id: str,
    ) -> ModerationCheckResponse:
        # `user_id` is a separate argument (not on `request`) so the
        # acting user can never be self-reported via the request body —
        # the route layer derives it from the Bearer token and hands it
        # in here. See `app.schemas.moderation.ModerationCheckRequest`
        # for the rationale on the schema-side drop.
        #
        # `is_minor` likewise comes from the JWT, not the body. We
        # default to False here so internal callers (turn_service /
        # sharecards) that haven't been threaded yet stay on the adult
        # tier — A-5 plumbs them through. The route layer always
        # passes the JWT-derived value.
        decision = await self._backend.evaluate(request.content, request.context)
        decision = _apply_minor_strictness(decision, is_minor=is_minor)

        try:
            await self._event_sink.record(
                request=request,
                user_id=user_id,
                decision=decision,
                backend_name=self._backend.name,
                trace_id=trace_id,
            )
        except Exception:
            # Audit failure must never block the response. The user
            # still gets the verdict; ops sees the structured log.
            logger.exception(
                "moderation_audit_failed",
                trace_id=trace_id,
                backend=self._backend.name,
            )

        return ModerationCheckResponse(
            verdict=decision.verdict,
            categories=list(decision.categories),
            score=decision.score,
            redirect_resource=decision.redirect_resource,
            trace_id=trace_id,
        )


def _apply_minor_strictness(decision: Decision, *, is_minor: bool) -> Decision:
    """Stricter post-processing for under-18 callers (PRD §3.0.5 C).

    Policy:
      * `allow`    → `allow`         (benign content stays unchanged)
      * `warn`     → **`block`**     (adults get a soft notice; for
                                       minors we err on the safe side
                                       and stop the content outright)
      * `redirect` → `redirect`      (PRESERVE — the redirect verdict
                                       carries a crisis-line resource;
                                       minors need that resource MORE,
                                       not less. Blocking would deny
                                       help to a user in distress.)
      * `block`    → `block`         (already strictest)

    Lowering the score threshold for what counts as `warn` vs `allow`
    would require backend cooperation and is deferred. The MVP gets
    the meaningful tighter outcome (warn → block) without touching the
    backends.
    """
    if not is_minor:
        return decision
    if decision.verdict == "warn":
        # `warn` decisions can carry categories but never a
        # redirect_resource (that's the `redirect` verdict's job), so
        # the elevated `block` decision drops nothing of value.
        return replace(decision, verdict="block")
    return decision
