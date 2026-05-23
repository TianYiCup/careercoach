"""Provider-neutral exception hierarchy for the TTS layer.

Mirrors `app.asr.errors` / `app.llm.errors` — adapters MUST map their
vendor-specific failures into one of these classes so the router /
failover layer can decide on the *type* of error rather than parsing a
vendor-specific status code.

Why the parallel shape: a future Langfuse trace tagger (the TTS analog
of `_classify_asr_error` in copilot.py) will branch on `isinstance(exc,
TTSAuthError | TTSTimeoutError | …)`; keeping the class names in step
with ASR / LLM means analysts don't have to learn a different vocabulary
per surface.
"""

from __future__ import annotations


class TTSError(Exception):
    """Base class for any failure raised by a `TTSProvider`."""

    def __init__(self, message: str, *, provider: str | None = None) -> None:
        super().__init__(message)
        self.provider = provider


class TTSTimeoutError(TTSError):
    """Upstream took longer than the configured timeout."""


class TTSAuthError(TTSError):
    """401/403 from the upstream — credentials missing or revoked."""


class TTSUpstreamError(TTSError):
    """Any other 4xx/5xx from the upstream that we don't handle specially.

    `status_code` may be:
      * 5xx — vendor-side failure (their fault, retry policy applies)
      * 4xx — request-side failure (our fault — bad request, rate
        limited, etc.; retry usually won't help)
      * None — we never got an HTTP-level response (network error,
        WebSocket abort, etc.) so we can't classify further

    Subclass `TTSMalformedResponseError` carries a different failure
    shape entirely (we GOT a response but couldn't parse it), so the
    isinstance ladder should check subclass first / status_code-bucket
    second — same order ASR uses.
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


class TTSMalformedResponseError(TTSUpstreamError):
    """The vendor replied but we couldn't parse the body.

    Distinct from a 5xx because the upstream THINKS it succeeded
    (returned a 200 / control frame indicating success), and distinct
    from a network error because we DID receive bytes back. Usually
    indicates a vendor SDK version drift or a partial outage where the
    response shape changed.
    """
