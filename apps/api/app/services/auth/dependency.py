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

from dataclasses import dataclass
from datetime import datetime

import structlog
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.services.auth.jwt_tokens import TokenPayload, decode_token
from app.services.auth.quiet_hours import _now_provider, is_in_minor_quiet_hours

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


@dataclass(frozen=True)
class CurrentUser:
    """The subset of JWT claims that route layers care about.

    * `is_minor` — drives the moderation strict tier (PRD §3.0.5 C).
    * `age_set`  — has the user declared `birth_year` yet? Drives the
                   compulsory age gate (PRD §1.5). Routes that handle
                   content depend on `require_age_set` to refuse
                   service until this is True.

    All three values are baked into the token at mint time and stay
    stable for the JWT lifetime (currently 30 days). Profile changes
    that flip either flag only take effect on the next mint —
    `update_birth_year` re-mints, so a user who sets their age sees
    the gate stop firing on their very next request.
    """

    user_id: str
    is_minor: bool
    age_set: bool


async def get_current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer_scheme),
) -> CurrentUser:
    """Return the authenticated user, or 401 if no valid token.

    Three branches:
      * No Authorization header → 401 UNAUTHORIZED
      * Invalid / expired / malformed token → 401 UNAUTHORIZED
      * Valid token → a `CurrentUser` carrying the `sub` + `is_minor`
        claims.
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

    return CurrentUser(
        user_id=payload.user_id,
        is_minor=payload.is_minor,
        age_set=payload.age_set,
    )


async def get_current_user_id(
    user: CurrentUser = Depends(get_current_user),
) -> str:
    """Back-compat dependency: return just the user id. Existing routes
    (`/sessions`, `/sharecards`, …) keep this signature so they don't
    need to know about the minor flag yet; routes that DO need it
    depend on `get_current_user` directly."""
    return user.user_id


async def require_age_set(
    user: CurrentUser = Depends(get_current_user),
) -> CurrentUser:
    """Compulsory age gate (PRD §1.5). Block content-handling routes
    until the user has declared `birth_year` via `POST /v1/users/me/
    birth-year`. Returns 403 AGE_REQUIRED if the JWT carries
    `age_set=False`.

    Why we don't auto-redirect: the client decides whether to surface
    an in-app age form, a hard wall, or a degraded read-only mode.
    Server-side we just refuse the request with a stable error code
    so the frontend has one place to handle it.

    Endpoints intentionally NOT gated:
      * `/v1/auth/*`            — that's how the user logs in
      * `/v1/users/me/birth-year` — that's how the user clears the gate
      * `/v1/scenarios` GET     — browsing the catalog is fine
      * `/v1/sessions/{id}/end` — already-started sessions must be
                                   end-able, otherwise an age set
                                   change mid-session strands the row
    """
    if not user.age_set:
        logger.info(
            "age_required",
            user_id=user.user_id,
            note="rejecting content-handling request until birth_year is declared",
        )
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "code": "AGE_REQUIRED",
                "message": "请先在「我的」中填写出生年份后再使用",
            },
        )
    return user


async def require_adult(
    user: CurrentUser = Depends(require_age_set),
) -> CurrentUser:
    """Gate for features that PRD §1.5 / §3.0.5 C bans for minors —
    primarily the 副驾 (live-coaching) flow which involves voice
    recording. Returns 403 MINOR_FORBIDDEN if the JWT says the caller
    is under 18.

    Chains AFTER `require_age_set` so a user who hasn't declared their
    birth year can't sneak in via the default `is_minor=False`
    fallback. Order matters:
      * no token                 → 401 UNAUTHORIZED (`get_current_user`)
      * token, age unset         → 403 AGE_REQUIRED  (`require_age_set`)
      * token, age set, minor    → 403 MINOR_FORBIDDEN
      * token, age set, adult    → pass

    The 副驾 stub route is wired in v0.1; this dependency is its
    primary compliance attach point (R-15).
    """
    if user.is_minor:
        logger.info(
            "minor_forbidden",
            user_id=user.user_id,
            note="rejecting minor from adult-only feature",
        )
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "code": "MINOR_FORBIDDEN",
                "message": "该功能未对未成年开放",
            },
        )
    return user


async def block_minor_quiet_hours(
    user: CurrentUser = Depends(require_age_set),
    now: datetime = Depends(_now_provider),
) -> CurrentUser:
    """Anti-addiction gate (PRD §1.5 防沉迷). Reject engagement
    endpoints (`POST /sessions`, `POST /turns`) for minor JWTs during
    Asia/Shanghai 22:00–08:00. Adults pass through unchanged.

    Chains AFTER `require_age_set` so:
      * no token              → 401 UNAUTHORIZED (`get_current_user`)
      * token but age unset   → 403 AGE_REQUIRED (`require_age_set`)
      * token, age set, minor, in quiet hours → 403 MINOR_QUIET_HOURS
      * everything else       → pass

    Note: the gate fires per-request, not per-session. A minor who
    enters a session at 21:55 and posts a turn at 22:05 gets blocked
    on the second call. That's intentional — the alternative ("once
    inside you finish") makes the policy easy to game by starting a
    session right before bedtime.
    """
    if user.is_minor and is_in_minor_quiet_hours(now):
        logger.info(
            "minor_quiet_hours_blocked",
            user_id=user.user_id,
            note="rejecting minor engagement during 22:00–08:00 Asia/Shanghai",
        )
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "code": "MINOR_QUIET_HOURS",
                "message": "未成年模式已开启夜间静默（22:00–次日 08:00），明早再来",
            },
        )
    return user


__all__ = [
    "ANONYMOUS_USER_ID",
    "CurrentUser",
    "block_minor_quiet_hours",
    "get_current_user",
    "get_current_user_id",
    "require_adult",
    "require_age_set",
]
