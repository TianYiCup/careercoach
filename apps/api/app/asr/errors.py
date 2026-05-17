"""Provider-neutral exception hierarchy for the ASR layer.

Mirrors `app.llm.errors` — adapters MUST map their vendor-specific
failures into one of these classes so any future router / failover
layer can make decisions on the *type* of error rather than parsing
a vendor-specific status code.
"""

from __future__ import annotations


class ASRError(Exception):
    """Base class for any failure raised by an `ASRProvider`."""

    def __init__(self, message: str, *, provider: str | None = None) -> None:
        super().__init__(message)
        self.provider = provider


class ASRTimeoutError(ASRError):
    """Upstream took longer than the configured timeout."""


class ASRAuthError(ASRError):
    """401/403 from the upstream — credentials missing or revoked."""


class ASRUpstreamError(ASRError):
    """Any other 4xx/5xx from the upstream that we don't handle specially.

    `status_code` may be:
      * 5xx — vendor-side failure (their fault, retry policy applies)
      * 4xx — request-side failure (our fault — bad request, rate
        limited, etc.; retry usually won't help)
      * None — we never got an HTTP-level response (network error,
        WebSocket abort, etc.) so we can't classify further

    A-38 uses these ranges in `_classify_asr_error` to subdivide the
    `upstream` tag bucket. Subclass `ASRMalformedResponseError` covers
    a different failure shape entirely (we GOT a response but couldn't
    parse it), so subclass first / status_code-bucket second is the
    classification order.
    """

    def __init__(
        self,
        message: str,
        *,
        provider: str | None = None,
        status_code: int | None = None,
    ) -> None:
        super().__init__(message, provider=provider)
        self.status_code = status_code


class ASRMalformedResponseError(ASRUpstreamError):
    """The vendor replied but we couldn't parse the body.

    Distinct from a 5xx because the upstream THINKS it succeeded
    (it returned a 200), and distinct from a network error because
    we DID receive bytes back. This usually indicates a vendor SDK
    version drift, a partial outage where the response shape changed,
    or a corrupted response.

    A-38 maps this to the `asr_error:upstream_malformed` Langfuse
    tag so ops can split parse failures from real 5xx/4xx outages
    when triaging — these often need different incident responses
    (vendor schema change ≠ vendor server down).
    """
