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
