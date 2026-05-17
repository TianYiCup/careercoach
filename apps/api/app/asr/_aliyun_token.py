"""Aliyun NLS access-token fetcher (RFC 3986 + HMAC-SHA1 + RPC POP).

Why we don't use `aliyun-python-sdk-core`
-----------------------------------------
The official Aliyun SDK is ~50 deps and brings in `aliyunsdkcore`,
`oss2`, and several gRPC modules we don't need. The STS-style POP
signing for the NLS token endpoint is one HMAC-SHA1, so we roll it
ourselves in ~80 LoC and skip the dependency.

Surface
-------
`fetch_access_token(...)` does one POST and returns `(token, expires_at)`
where `expires_at` is a UNIX epoch in seconds. `AliyunTokenCache` wraps
it so the WS adapter doesn't sign+round-trip on every connect — Aliyun
tokens are valid for ~24h and the cache keeps the live one until 30s
before expiry, then refreshes lazily.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import time
import urllib.parse
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime

import httpx
import structlog

from app.asr.errors import ASRAuthError, ASRTimeoutError, ASRUpstreamError

logger = structlog.get_logger(__name__)

PROVIDER_NAME = "aliyun"

# Refresh this many seconds BEFORE the token's stated expiry so a
# request in-flight when the token rolls over still sees a fresh
# value. 30s is plenty given Aliyun's clock skew tolerance.
_REFRESH_BUFFER_SECONDS = 30.0

# RPC API constants — fixed by Aliyun's signing spec for v1 POP APIs.
_SIGNATURE_METHOD = "HMAC-SHA1"
_SIGNATURE_VERSION = "1.0"
_API_VERSION = "2019-02-28"
_API_ACTION = "CreateToken"
_API_FORMAT = "JSON"


@dataclass(frozen=True)
class AccessToken:
    """One issued NLS access token + its UNIX-epoch expiry."""

    token: str
    expires_at: float

    def is_fresh(self, *, now: float | None = None) -> bool:
        """`True` when the token has more than `_REFRESH_BUFFER_SECONDS` of life left."""
        current = now if now is not None else time.time()
        return self.expires_at - current > _REFRESH_BUFFER_SECONDS


class AliyunTokenCache:
    """Single-token TTL cache. Not goroutine-safe per Aliyun's design —
    we hold one connection process-wide and only ever refresh under
    a single asyncio task.

    The cache is opt-in: pass `cached_token=` to seed it (useful for
    tests) and call `get()` to lazily refresh-or-return.
    """

    def __init__(self, *, cached_token: AccessToken | None = None) -> None:
        self._cached = cached_token

    async def get(
        self,
        *,
        access_key_id: str,
        access_key_secret: str,
        endpoint_url: str,
        timeout_s: float,
        client: httpx.AsyncClient | None = None,
    ) -> AccessToken:
        """Return a fresh token, fetching from Aliyun if needed."""
        if self._cached is not None and self._cached.is_fresh():
            return self._cached
        token = await fetch_access_token(
            access_key_id=access_key_id,
            access_key_secret=access_key_secret,
            endpoint_url=endpoint_url,
            timeout_s=timeout_s,
            client=client,
        )
        self._cached = token
        return token


async def fetch_access_token(
    *,
    access_key_id: str,
    access_key_secret: str,
    endpoint_url: str,
    timeout_s: float = 5.0,
    client: httpx.AsyncClient | None = None,
) -> AccessToken:
    """POST to the NLS token endpoint with a POP-style HMAC-SHA1 signature.

    The signing is fully canonical-query-string based — no body, no
    headers beyond `Host`. See Aliyun's NLS docs for the canonical
    parameter set; missing or wrong-case params produce a 400 with
    `Signature does not match`.
    """
    if not access_key_id or not access_key_secret:
        # Fail before the network call — caller can map this to a
        # clearer configuration error than the upstream's "InvalidAccessKeyId".
        raise ASRAuthError(
            "aliyun ASR token: AK/secret are empty",
            provider=PROVIDER_NAME,
        )

    params = _canonical_token_params(access_key_id=access_key_id)
    signature = _sign_pop_query(
        method="POST",
        params=params,
        secret=access_key_secret,
    )
    signed = {**params, "Signature": signature}

    owns_client = client is None
    if owns_client:
        client = httpx.AsyncClient(timeout=timeout_s)
    assert client is not None  # for type checkers
    try:
        try:
            response = await client.post(endpoint_url, data=signed, timeout=timeout_s)
        except httpx.TimeoutException as exc:
            raise ASRTimeoutError(
                f"aliyun ASR token request exceeded {timeout_s}s",
                provider=PROVIDER_NAME,
            ) from exc
        except httpx.HTTPError as exc:
            raise ASRUpstreamError(
                f"aliyun ASR token transport error: {exc}",
                provider=PROVIDER_NAME,
            ) from exc

        if response.status_code in (401, 403):
            raise ASRAuthError(
                f"aliyun ASR token auth failed ({response.status_code}): {response.text[:200]}",
                provider=PROVIDER_NAME,
            )
        if response.status_code >= 400:
            raise ASRUpstreamError(
                f"aliyun ASR token http {response.status_code}: {response.text[:200]}",
                provider=PROVIDER_NAME,
                status_code=response.status_code,
            )

        return _parse_token_response(response.json())
    finally:
        if owns_client:
            await client.aclose()


def _canonical_token_params(*, access_key_id: str) -> dict[str, str]:
    """Build the canonical parameter dict (excluding `Signature` itself)."""
    now_iso = datetime.now(tz=UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    return {
        "Format": _API_FORMAT,
        "Version": _API_VERSION,
        "AccessKeyId": access_key_id,
        "SignatureMethod": _SIGNATURE_METHOD,
        "Timestamp": now_iso,
        "SignatureVersion": _SIGNATURE_VERSION,
        "SignatureNonce": uuid.uuid4().hex,
        "Action": _API_ACTION,
    }


def _sign_pop_query(*, method: str, params: dict[str, str], secret: str) -> str:
    """POP signature: HMAC-SHA1(secret + "&", METHOD + "&" + encode("/") + "&" + encoded_canonical_query)."""
    sorted_items = sorted(params.items())
    encoded_query = "&".join(f"{_pop_encode(k)}={_pop_encode(v)}" for k, v in sorted_items)
    string_to_sign = f"{method}&{_pop_encode('/')}&{_pop_encode(encoded_query)}"
    signing_key = (secret + "&").encode("utf-8")
    digest = hmac.new(signing_key, string_to_sign.encode("utf-8"), hashlib.sha1).digest()
    return base64.b64encode(digest).decode("ascii")


def _pop_encode(value: str) -> str:
    """Aliyun POP percent-encoding: like RFC 3986 but with `+` → `%20` and
    `*` → `%2A` and `~` left as-is. `urllib.parse.quote(safe="~")`
    gives `+` for spaces, which Aliyun rejects, so we post-process."""
    return urllib.parse.quote(value, safe="~").replace("+", "%20").replace("*", "%2A")


def _parse_token_response(payload: object) -> AccessToken:
    """Lift the Aliyun JSON response into our `AccessToken`.

    Successful response shape (per Aliyun NLS docs):
        {"Token": {"Id": "...", "ExpireTime": 1234567890, "UserId": "..."},
         "NlsRequestId": "...", "ErrMsg": ""}
    """
    if not isinstance(payload, dict):
        raise ASRUpstreamError(
            "aliyun ASR token response was not a JSON object",
            provider=PROVIDER_NAME,
        )
    token_block = payload.get("Token")
    if not isinstance(token_block, dict):
        # Surface the upstream's error message when present — easier
        # debugging than "unexpected shape".
        err = payload.get("ErrMsg") or payload.get("Message") or "no Token block"
        raise ASRUpstreamError(
            f"aliyun ASR token response missing Token: {err!r}",
            provider=PROVIDER_NAME,
        )
    token_id = token_block.get("Id")
    expire_time = token_block.get("ExpireTime")
    if not isinstance(token_id, str) or not token_id:
        raise ASRUpstreamError(
            "aliyun ASR token Id missing or non-string",
            provider=PROVIDER_NAME,
        )
    try:
        expires_at = float(expire_time)  # type: ignore[arg-type]
    except (TypeError, ValueError) as exc:
        raise ASRUpstreamError(
            f"aliyun ASR token ExpireTime not numeric: {expire_time!r}",
            provider=PROVIDER_NAME,
        ) from exc
    return AccessToken(token=token_id, expires_at=expires_at)


__all__ = [
    "AccessToken",
    "AliyunTokenCache",
    "fetch_access_token",
]
