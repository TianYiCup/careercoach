"""FastAPI dependency that resolves the current user id from a Bearer token.

Soft policy in this PR: a missing or invalid token logs a warning and
returns the `"anonymous"` sentinel so the route can still serve the
request. The follow-up PR flips this to a hard 401 once the frontend
ships its bearer-token client and we've watched anon traffic drop to
zero in logs.

Why a soft transition: B's `apps/web/src/api/v1/client.ts` doesn't send
`Authorization` headers yet. Hard-enforcing here would block every
sandbox session route until B catches up. The sentinel keeps the route
behaviour identical to today's anonymous wiring while letting real
tokens flow through as soon as the frontend can mint them.

The `Bearer <token>` header parsing uses FastAPI's built-in
`HTTPBearer` so OpenAPI gets the right `securitySchemes` entry — B's
codegen will know to wire an Authorization picker as soon as we flip
to required.
"""

from __future__ import annotations

import structlog
from fastapi import Depends
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.services.auth.jwt_tokens import TokenPayload, decode_token

logger = structlog.get_logger(__name__)

ANONYMOUS_USER_ID = "anonymous"

# `auto_error=False` so a missing header doesn't 403 — the soft-mode
# dependency interprets None as anonymous instead. When we flip to
# hard auth, just remove this flag.
_bearer_scheme = HTTPBearer(auto_error=False, scheme_name="JWT")


async def get_current_user_id(
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer_scheme),
) -> str:
    """Return the user id behind the request.

    Soft transition policy (this PR):
      * No Authorization header → ANONYMOUS_USER_ID + `auth_missing` log
      * Invalid / expired token → ANONYMOUS_USER_ID + `auth_invalid` log
      * Valid token → the `sub` claim

    The follow-up PR will lift this to raise 401 in the first two cases.
    """
    if credentials is None:
        logger.info("auth_missing", note="anonymous fallback active")
        return ANONYMOUS_USER_ID

    payload: TokenPayload | None = decode_token(credentials.credentials)
    if payload is None:
        # decode_token already logged the reason — we just record that
        # the soft fallback kicked in so the next PR can monitor it.
        logger.info("auth_invalid", note="anonymous fallback active")
        return ANONYMOUS_USER_ID

    return payload.user_id


__all__ = ["ANONYMOUS_USER_ID", "get_current_user_id"]
