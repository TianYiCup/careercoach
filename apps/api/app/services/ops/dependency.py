"""Ops-side auth dependency — gates `/v1/ops/*` endpoints with a
static `X-Ops-Token` header.

A-41 ships only the dep so the auth shape can be reviewed in
isolation; A-42 attaches it to the cost rollup endpoint.

Why a separate auth scheme rather than reusing JWT
--------------------------------------------------
User JWTs are minted at login and carry per-user claims; they don't
encode role/scope. Adding a "staff" claim to JWTs would mean every
session of every employee needs a special mint path, and any future
JWT-decode bug could escalate a normal user to ops. Keeping the
ops path on a dedicated header isolates the blast radius — a
compromised user JWT can never reach ops, and a leaked ops token
can be rotated without re-minting every user JWT.

Why fail-closed when unconfigured
---------------------------------
`ops_api_token` defaults to empty in `Settings`. If the deployment
forgets to set `OPS_API_TOKEN`, the dep returns 503 (with code
`OPS_DISABLED`) rather than treating empty as "any token passes" or
"no token required". A forgotten env var must never silently open
the rollup endpoint to the public.

Why one error code for both missing + wrong header
--------------------------------------------------
Splitting `OPS_TOKEN_MISSING` vs `OPS_TOKEN_INVALID` would let an
attacker tell whether they had the header shape right vs the value
right. Single `OPS_AUTH_REQUIRED` 401 with `WWW-Authenticate: Ops`
matches the deny-by-default posture in `dependency.get_current_user`.
"""

from __future__ import annotations

import hmac

import structlog
from fastapi import Header, HTTPException, status

from app.config import get_settings

logger = structlog.get_logger(__name__)


async def require_ops_token(
    x_ops_token: str | None = Header(default=None, alias="X-Ops-Token"),
) -> None:
    """Reject the request unless the `X-Ops-Token` header matches the
    configured `settings.ops_api_token`.

    Three branches:
      * `ops_api_token` empty in this deployment → 503 OPS_DISABLED
      * Header missing OR mismatched           → 401 OPS_AUTH_REQUIRED
      * Match                                  → return (proceed)

    Constant-time comparison (`hmac.compare_digest`) prevents timing
    side-channels even though the token is short and the rollup
    endpoint isn't a high-rate target — paranoid by default is
    cheaper than auditing later.
    """
    configured = get_settings().ops_api_token.get_secret_value()
    if not configured:
        logger.info("ops_disabled", note="ops endpoints rejected — OPS_API_TOKEN unset")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "code": "OPS_DISABLED",
                "message": "ops endpoints are not enabled in this deployment",
            },
        )

    presented = x_ops_token or ""
    if not hmac.compare_digest(presented, configured):
        # Single log + single status for both missing-header and
        # wrong-value to avoid leaking which one failed. The presented-
        # value is NEVER logged (would land in observability with
        # whatever the attacker tried).
        logger.info(
            "ops_auth_rejected",
            had_header=bool(x_ops_token),
            note="rejecting ops request — token missing or mismatched",
        )
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={
                "code": "OPS_AUTH_REQUIRED",
                "message": "valid X-Ops-Token header required",
            },
            headers={"WWW-Authenticate": "Ops"},
        )


__all__ = ["require_ops_token"]
