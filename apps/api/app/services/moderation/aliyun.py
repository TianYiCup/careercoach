"""Aliyun Content Moderation 2.0 adapter.

Endpoint
--------
POST https://{endpoint}/   (action = TextModeration, version = 2022-03-02)

Body shape:

    {
      "Service": "chat_detection",
      "ServiceParameters": "{\\"content\\": \\"...\\"}"
    }

Response (success, Content-Moderation-2.0 / 增强版):

    {
      "RequestId": "...",
      "Code": 200,
      "Data": {
        "labels": "sexual_content,profanity",
        "reason": "{\\"riskLevel\\":\\"high\\", ...}",
        "descriptions": "..."
      }
    }

`Data.labels` is a comma-separated string of label names and
`Data.reason.riskLevel` is the single severity for the whole text —
the legacy `Data.Result[].{Label,Confidence}` shape is gone.

Label mapping
-------------
We collapse the labels into our six `ModerationCategory` buckets.
Anything that doesn't fall into one of our red-line categories is
silently dropped — `verdict=allow` because no red-line category was
attached. CRITICAL: this scene does NOT label self-harm, so cloud
`allow` is never trusted blindly — `CascadingBackend` re-checks the
local dict (the §3.0.5 red-line floor) on every cloud allow.

Signing
-------
ACS3-HMAC-SHA256 (Aliyun v3). The 60-odd lines in `_sign_request` are
the entire algorithm — no SDK dep.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import uuid
from datetime import UTC, datetime
from typing import Any

import httpx
import structlog

from app.config import Settings
from app.schemas.moderation import ModerationCategory, ModerationContext
from app.services.moderation.backend import ModerationBackendError
from app.services.moderation.local_dict import SELF_HARM_RESOURCE
from app.services.moderation.types import Decision

logger = structlog.get_logger(__name__)

ACTION = "TextModeration"
VERSION = "2022-03-02"
SIG_ALGO = "ACS3-HMAC-SHA256"

# Aliyun label → our ModerationCategory. Anything missing → dropped.
# Labels come from the Content-Moderation-2.0 (增强版) `chat_detection`
# `Data.labels` field — these are the NEW names (e.g. `profanity`,
# `political_content`), distinct from the legacy green-cip label sheet.
_LABEL_TO_CATEGORY: dict[str, ModerationCategory] = {
    # self-harm / suicidal ideation — the 增强版 chat scene does NOT
    # reliably emit a self-harm label; this stays mapped defensively in
    # case the rule adds one, but the local-dict floor in CascadingBackend
    # is what actually guarantees self-harm recall.
    "self_harm": "self_harm",
    "self_harm_content": "self_harm",
    # violence / abuse
    "profanity": "violence",  # 辱骂 / 脏话 / 人身攻击
    "violence": "violence",
    "violent_content": "violence",
    # loan / contraband (drugs, gambling, illegal goods → loan bucket)
    "contraband": "loan",
    "contraband_content": "loan",
    "ad": "loan",  # promotional spam, often loan / gambling ads
    # sexual / harassment
    "sexual_content": "harassment",
    "porn": "harassment",
    # political
    "political_content": "political",
    "political": "political",
}

# Legacy: the old green-cip scene scored each label with a Confidence in
# `Result[]`. The 增强版 response has no per-label confidence (it returns
# a single `riskLevel` instead), so this is unused by the new parser; the
# constructor keeps it only to preserve its signature.
_DEFAULT_HIT_CONFIDENCE = 80.0

# Risk levels (`Data.reason.riskLevel`) that count as a hard hit. `low`
# is surfaced as `warn` (soft); anything below / empty is `allow`.
_HARD_RISK_LEVELS = frozenset({"high", "medium"})

CLOUD_SCORE = 0.99
# Soft score for `low` riskLevel hits surfaced as `warn`.
CLOUD_WARN_SCORE = 0.6
"""Aliyun decisions get a higher score than local dict so audit logs can rank."""


class AliyunTextModerationBackend:
    """Streaming-free cloud backend.

    Structurally implements `app.services.moderation.ModerationBackend`.
    """

    name: str = "aliyun"

    def __init__(
        self,
        *,
        access_key_id: str,
        access_key_secret: str,
        endpoint: str,
        service: str,
        timeout_s: float,
        hit_confidence: float = _DEFAULT_HIT_CONFIDENCE,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        if not access_key_id or not access_key_secret:
            raise ValueError("AliyunTextModerationBackend requires non-empty credentials")
        self._ak_id = access_key_id
        self._ak_secret = access_key_secret
        self._endpoint = endpoint.rstrip("/")
        self._service = service
        self._timeout_s = timeout_s
        self._hit_confidence = hit_confidence
        self._owned_client = client is None
        self._client = client or httpx.AsyncClient(timeout=timeout_s)

    @classmethod
    def from_settings(cls, settings: Settings) -> AliyunTextModerationBackend:
        return cls(
            access_key_id=settings.aliyun_access_key_id.get_secret_value(),
            access_key_secret=settings.aliyun_access_key_secret.get_secret_value(),
            endpoint=settings.aliyun_moderation_endpoint,
            service=settings.aliyun_moderation_service,
            timeout_s=settings.aliyun_moderation_timeout_s,
        )

    async def aclose(self) -> None:
        if self._owned_client:
            await self._client.aclose()

    async def evaluate(self, content: str, context: ModerationContext) -> Decision:
        _ = context  # Aliyun scene is configured once at init; not per-call yet.

        body = json.dumps(
            {
                "Service": self._service,
                "ServiceParameters": json.dumps({"content": content}, ensure_ascii=False),
            },
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode("utf-8")

        headers = _sign_request(
            method="POST",
            host=self._endpoint,
            body=body,
            access_key_id=self._ak_id,
            access_key_secret=self._ak_secret,
        )

        try:
            response = await self._client.post(
                f"https://{self._endpoint}/",
                content=body,
                headers=headers,
            )
        except httpx.TimeoutException as exc:
            raise ModerationBackendError(
                f"aliyun timed out after {self._timeout_s}s",
                backend=self.name,
            ) from exc
        except httpx.HTTPError as exc:
            raise ModerationBackendError(
                f"aliyun transport error: {exc!s}", backend=self.name
            ) from exc

        if response.status_code != 200:
            raise ModerationBackendError(
                f"aliyun returned HTTP {response.status_code}: {response.text[:200]}",
                backend=self.name,
            )

        return _decision_from_response(response.json())


def _sign_request(
    *,
    method: str,
    host: str,
    body: bytes,
    access_key_id: str,
    access_key_secret: str,
) -> dict[str, str]:
    """Build ACS3-HMAC-SHA256 signed headers.

    Aliyun POP v3 spec — see the developer docs page
    `https://help.aliyun.com/.../sign-api-requests`. The algorithm
    is the same shape as AWS sigv4 minus the regional scope:

        canonical_request = METHOD\\nURI\\nQS\\nHEADERS\\nSIGNED\\nPAYLOAD_SHA256
        string_to_sign   = "ACS3-HMAC-SHA256\\n" + sha256(canonical_request)
        signature        = hex(HMAC-SHA256(secret, string_to_sign))
    """
    body_sha = hashlib.sha256(body).hexdigest()
    nonce = uuid.uuid4().hex
    date = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")

    headers_to_sign: dict[str, str] = {
        "host": host,
        "x-acs-action": ACTION,
        "x-acs-content-sha256": body_sha,
        "x-acs-date": date,
        "x-acs-signature-nonce": nonce,
        "x-acs-version": VERSION,
    }
    sorted_keys = sorted(headers_to_sign.keys())
    canonical_headers = "".join(f"{k}:{headers_to_sign[k]}\n" for k in sorted_keys)
    signed_headers = ";".join(sorted_keys)

    canonical_request = f"{method}\n/\n\n{canonical_headers}\n{signed_headers}\n{body_sha}"
    string_to_sign = f"{SIG_ALGO}\n{hashlib.sha256(canonical_request.encode()).hexdigest()}"
    signature = hmac.new(
        access_key_secret.encode("utf-8"),
        string_to_sign.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()

    authorization = (
        f"{SIG_ALGO} Credential={access_key_id},"
        f"SignedHeaders={signed_headers},"
        f"Signature={signature}"
    )

    return {
        **{k: v for k, v in headers_to_sign.items() if k != "host"},
        "Authorization": authorization,
        "Content-Type": "application/json",
    }


def _decision_from_response(payload: dict[str, Any]) -> Decision:
    """Translate a Content-Moderation-2.0 (增强版) response into a Decision.

    Response shape (success):

        {"Code": 200, "Data": {
            "labels": "sexual_content,profanity",
            "reason": "{\\"riskLevel\\":\\"high\\", ...}",
            "descriptions": "..."}}

    `Data.labels` is a comma-separated string of the NEW label names;
    `Data.reason.riskLevel` (a JSON string) carries the single severity
    for the whole text — there is no per-label confidence any more.
    """
    code = payload.get("Code")
    if code != 200:
        raise ModerationBackendError(
            f"aliyun returned non-200 Code: {code} {payload.get('Message')}",
            backend="aliyun",
        )

    data = payload.get("Data") or {}
    risk_level = _risk_level_of(data)
    categories: set[ModerationCategory] = set()
    for raw_label in str(data.get("labels") or "").split(","):
        label = raw_label.strip()
        if not label:
            continue
        mapped = _LABEL_TO_CATEGORY.get(label)
        if mapped is not None:
            categories.add(mapped)

    if not categories:
        return Decision(verdict="allow", score=0.0)

    # Self_harm wins regardless of riskLevel — same priority rule as
    # DictBackend, and the hotline resource is the shared
    # `SELF_HARM_RESOURCE` so both backends surface identical UI copy.
    if "self_harm" in categories:
        return Decision(
            verdict="redirect",
            score=CLOUD_SCORE,
            categories=tuple(sorted(categories)),
            redirect_resource=SELF_HARM_RESOURCE,
        )

    # `low` risk → soft `warn` (e.g. a generic promo `ad`); high/medium
    # → hard `block`. The local-dict floor in CascadingBackend still
    # re-checks every cloud `allow`, so a missed hard red-line (notably
    # self-harm, which this scene doesn't label) can't slip through.
    if risk_level not in _HARD_RISK_LEVELS:
        return Decision(
            verdict="warn",
            score=CLOUD_WARN_SCORE,
            categories=tuple(sorted(categories)),
        )

    return Decision(
        verdict="block",
        score=CLOUD_SCORE,
        categories=tuple(sorted(categories)),
    )


def _risk_level_of(data: dict[str, Any]) -> str:
    """Pull `riskLevel` out of the JSON-encoded `Data.reason` string.

    Returns "" when reason is absent or unparseable — the caller then
    treats it as a non-hard level (soft `warn` at most)."""
    reason = data.get("reason")
    if not isinstance(reason, str):
        return ""
    try:
        parsed = json.loads(reason)
    except (json.JSONDecodeError, ValueError):
        return ""
    if isinstance(parsed, dict):
        level = parsed.get("riskLevel")
        return level if isinstance(level, str) else ""
    return ""
