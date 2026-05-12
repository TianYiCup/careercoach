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

import structlog

from app.schemas.moderation import ModerationCheckRequest, ModerationCheckResponse
from app.services.moderation.backend import ModerationBackend
from app.services.moderation.event_sink import ModerationEventSink

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
        trace_id: str,
    ) -> ModerationCheckResponse:
        decision = await self._backend.evaluate(request.content, request.context)

        try:
            await self._event_sink.record(
                request=request,
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
