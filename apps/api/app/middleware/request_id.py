"""Bind a `request_id` to every request and surface it everywhere.

Before this middleware existed, each route called
`request.headers.get("x-request-id") or str(uuid.uuid4())` on its own,
which means a single request that touched two handlers (e.g. error
envelope + route handler) generated TWO different uuids and the log
lines drifted apart. This module centralises the seam:

  * On request: take the inbound `X-Request-Id` if it's a sane format,
    otherwise mint a fresh uuid. Stash on `request.state.request_id`
    and bind to structlog's contextvars so every log line emitted
    during the request inherits it automatically.
  * On response: echo the id back in the `X-Request-Id` header so
    clients can correlate.

`get_request_id(request)` is the recommended accessor for handlers —
falls back to a fresh uuid only if the middleware wasn't installed
(shouldn't happen in production, but keeps unit tests resilient).
"""

from __future__ import annotations

import re
import uuid
from collections.abc import Awaitable, Callable

import structlog
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

logger = structlog.get_logger(__name__)

REQUEST_ID_HEADER = "X-Request-Id"
"""Inbound + outbound header name. Frontend B sends this when it's
correlating across SSE retries; LB / nginx will also pass it through."""

REQUEST_ID_STATE_KEY = "request_id"
"""Attribute on `request.state` where the resolved id lives so handlers
can read it without re-parsing the header."""

# Strict-ish gate on incoming ids: keep them URL-safe and reasonable
# length so an attacker can't inject log-corrupting payloads. Real
# uuids and `req_<hex>` patterns both pass.
_VALID_INBOUND_PATTERN = re.compile(r"^[A-Za-z0-9_-]{8,128}$")


class RequestIdMiddleware(BaseHTTPMiddleware):
    """ASGI middleware that resolves + binds + echoes the request id.

    Install via `app.add_middleware(RequestIdMiddleware)` — must be
    near the OUTER edge so handlers and the global exception logger
    both see the binding. Sentry's ASGI middleware can sit outside or
    inside; either way trace_ids show up in both.
    """

    async def dispatch(
        self,
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        request_id = _resolve_request_id(request)
        setattr(request.state, REQUEST_ID_STATE_KEY, request_id)

        # `bind_contextvars` is process-wide; clear at end so a worker
        # reused between requests doesn't leak the previous id.
        structlog.contextvars.clear_contextvars()
        structlog.contextvars.bind_contextvars(request_id=request_id)
        try:
            response = await call_next(request)
        finally:
            structlog.contextvars.clear_contextvars()

        response.headers[REQUEST_ID_HEADER] = request_id
        return response


def get_request_id(request: Request) -> str:
    """Read the resolved request id off `request.state`.

    Falls back to a fresh uuid if the middleware isn't installed —
    legitimate only in unit tests. Production must always install
    `RequestIdMiddleware`.
    """
    resolved = getattr(request.state, REQUEST_ID_STATE_KEY, None)
    if isinstance(resolved, str) and resolved:
        return resolved
    # The middleware is the only sanctioned writer; reaching here
    # means a test built a Request directly. Log so we notice if it
    # happens in prod traffic.
    fallback = str(uuid.uuid4())
    logger.warning(
        "request_id_middleware_not_installed",
        fallback_request_id=fallback,
    )
    return fallback


def _resolve_request_id(request: Request) -> str:
    """Use the inbound header if it's well-formed, otherwise mint one.

    Rejecting malformed headers prevents log-injection (e.g. embedded
    newlines, ANSI control codes) and also catches accidental
    placeholder values like `null` or `undefined` from the client.
    """
    inbound = request.headers.get(REQUEST_ID_HEADER)
    if inbound and _VALID_INBOUND_PATTERN.fullmatch(inbound):
        return inbound
    return str(uuid.uuid4())
