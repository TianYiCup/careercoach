"""JWT minting + decoding for the phone-auth flow.

Tokens are HS256-signed with `settings.jwt_secret`. Claims:

  * `iss`  — fixed string `careercoach-api`, makes log grep + audience
              checks unambiguous if other services start signing later.
  * `sub`  — user UUID string. Routes use this to look up the row.
  * `iat`  — issued-at UTC seconds.
  * `exp`  — expiry, default 30 days out (PRD §7.2 — "stay signed in").
  * `persona_type` + `is_minor` — denormalised onto the token so the
    minor-mode gate (red-line) doesn't need a DB round-trip per request.

The denormalisation is safe because both fields are stable for the
session lifetime; if a user re-onboards their persona, the next mint
picks up the change.

Decoding is non-throwing on bad/expired tokens — `decode_token` returns
`None` and the route layer maps that to a 401. Surfacing concrete jose
exceptions to the route would leak the failure reason (which is fine
for an attacker; bad for our log discipline).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import structlog
from jose import JWTError, jwt

from app.config import get_settings

logger = structlog.get_logger(__name__)

_ALGORITHM = "HS256"
_ISSUER = "careercoach-api"
_DEFAULT_TTL = timedelta(days=30)


@dataclass(frozen=True)
class TokenPayload:
    """Decoded claims a route handler can rely on without re-validating."""

    user_id: str
    persona_type: str
    is_minor: bool
    expires_at: datetime


def mint_token(
    *,
    user_id: str,
    persona_type: str,
    is_minor: bool,
    ttl: timedelta = _DEFAULT_TTL,
    now: datetime | None = None,
) -> str:
    """Return a signed JWT. `now` is injectable so tests can pin issuance time."""
    issued_at = now or datetime.now(UTC)
    expires_at = issued_at + ttl
    claims: dict[str, object] = {
        "iss": _ISSUER,
        "sub": user_id,
        "iat": int(issued_at.timestamp()),
        "exp": int(expires_at.timestamp()),
        "persona_type": persona_type,
        "is_minor": is_minor,
    }
    return jwt.encode(claims, get_settings().jwt_secret, algorithm=_ALGORITHM)


def decode_token(token: str) -> TokenPayload | None:
    """Return the decoded payload, or `None` if the token is invalid /
    expired / wrong issuer. Never raises — the route maps None to 401.

    We deliberately swallow `JWTError` rather than typing it back to the
    caller: every reason a token is rejected (bad signature, expired,
    missing claim, wrong issuer) gets the same external response.
    """
    try:
        claims = jwt.decode(
            token,
            get_settings().jwt_secret,
            algorithms=[_ALGORITHM],
            issuer=_ISSUER,
        )
    except JWTError as exc:
        logger.info("jwt_decode_failed", reason=type(exc).__name__)
        return None

    user_id = claims.get("sub")
    persona_type = claims.get("persona_type")
    expires_at_ts = claims.get("exp")
    if not isinstance(user_id, str) or not isinstance(persona_type, str):
        logger.warning("jwt_missing_claims", claims=list(claims.keys()))
        return None
    if not isinstance(expires_at_ts, int):
        logger.warning("jwt_missing_exp", claims=list(claims.keys()))
        return None

    return TokenPayload(
        user_id=user_id,
        persona_type=persona_type,
        is_minor=bool(claims.get("is_minor", False)),
        expires_at=datetime.fromtimestamp(expires_at_ts, tz=UTC),
    )


__all__ = ["TokenPayload", "decode_token", "mint_token"]
