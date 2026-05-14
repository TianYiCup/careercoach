"""FastAPI dependency that resolves the current user id from a Bearer token.

Hard policy: a missing or invalid token raises 401 UNAUTHORIZED so the
caller knows to obtain credentials before retrying. The anonymous
sentinel survives in the codebase as a *data* value (for legacy session
rows minted in soft-mode) but is no longer a valid runtime outcome of
this dependency.

The previous soft transition existed because B's
`apps/web/src/api/v1/client.ts` didn't send `Authorization` headers yet.
Now that B's client mints and sends Bearer tokens (PR #55), enforcing
401 here matches OWASP's "deny by default" posture without breaking any
real caller.

The `Bearer <token>` header parsing uses FastAPI's built-in
`HTTPBearer` so OpenAPI gets the right `securitySchemes` entry — B's
codegen picks up the requirement automatically.
"""

from __future__ import annotations

import structlog
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.services.auth.jwt_tokens import TokenPayload, decode_token

logger = structlog.get_logger(__name__)

# Legacy sentinel kept around for the audit/log path: session rows
# created during the soft-mode window carry this value, and routes that
# fall back to it on legacy data shouldn't crash. The dependency below
# does NOT return this value anymore.
ANONYMOUS_USER_ID = "anonymous"

# `auto_error=False` lets us raise our own structured 401 (matching the
# PRD §7.1 error envelope) instead of FastAPI's default 403 from
# `HTTPBearer`'s built-in error path.
_bearer_scheme = HTTPBearer(auto_error=False, scheme_name="JWT")


async def get_current_user_id(
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer_scheme),
) -> str:
    """Return the user id behind the request, or 401 if no valid token.

    Three branches:
      * No Authorization header → 401 UNAUTHORIZED
      * Invalid / expired / malformed token → 401 UNAUTHORIZED
      * Valid token → the `sub` claim
    """
    if credentials is None:
        logger.info("auth_missing", note="rejecting unauthenticated request")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"code": "UNAUTHORIZED", "message": "missing bearer token"},
            headers={"WWW-Authenticate": "Bearer"},
        )

    payload: TokenPayload | None = decode_token(credentials.credentials)
    if payload is None:
        # decode_token already logged the reason — we just record that
        # the hard 401 fired so dashboards can separate "no header" from
        # "bad header".
        logger.info("auth_invalid", note="rejecting request with bad token")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"code": "UNAUTHORIZED", "message": "invalid or expired token"},
            headers={"WWW-Authenticate": "Bearer"},
        )

    return payload.user_id


__all__ = ["ANONYMOUS_USER_ID", "get_current_user_id"]
