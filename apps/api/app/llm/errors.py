"""Provider-neutral exception hierarchy for the LLM layer.

The router (D3-A-4) decides whether to fail over based on the *type*
of error, never the upstream's raw status code. Adapters MUST map
their vendor-specific failures into one of these classes so the
router's policy stays portable.
"""

from __future__ import annotations


class LLMError(Exception):
    """Base class for any failure raised by an `LLMProvider`."""

    def __init__(self, message: str, *, provider: str | None = None) -> None:
        super().__init__(message)
        self.provider = provider


class LLMTimeoutError(LLMError):
    """Upstream took longer than the configured timeout."""


class LLMAuthError(LLMError):
    """401/403 from the upstream — credentials missing or revoked."""


class LLMRateLimitError(LLMError):
    """429 from the upstream — caller should back off or fail over."""


class LLMUpstreamError(LLMError):
    """Any other 4xx/5xx from the upstream that we don't handle specially."""

    def __init__(
        self,
        message: str,
        *,
        provider: str | None = None,
        status_code: int | None = None,
    ) -> None:
        super().__init__(message, provider=provider)
        self.status_code = status_code
