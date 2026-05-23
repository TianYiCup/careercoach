"""Text-to-speech — PRD US-B2.

`POST /v1/tts/synthesize` takes a Mandarin hint, runs it through the
red-line moderation gate, and streams synthesized audio back. The
endpoint is the canonical TTS surface for both the copilot earphone
whisper (the original US-B2 driver) and any sandbox/review surface
that later wants to speak K's lines instead of just printing them.

Compliance attach point
-----------------------
`require_adult` chains through `require_age_set`, so the failure
ladder mirrors copilot:

  * No token              → 401 UNAUTHORIZED
  * Token, age unset      → 403 AGE_REQUIRED
  * Token, is_minor=True  → 403 MINOR_FORBIDDEN
  * Token, adult, age set → 200 stream

US-B1 / R-15 require copilot to be adult-only; since TTS exists today
solely to drive copilot's audio surface, the same gate applies. A
future PR can split this if a TTS use case appears that should be
available to minors (none on the v1 roadmap).

Moderation
----------
Input moderation runs before any TTS bytes are spent — `block` /
`redirect` verdicts return 422 with a stable error code so the client
distinguishes "your text was rejected" from "the TTS vendor is down"
(the latter returns 503). `warn` and `allow` proceed to synthesis.

Why a separate REST endpoint vs splicing audio into the copilot WS
-----------------------------------------------------------------
The copilot WS already streams text deltas the client can render
incrementally; bolting audio frames onto the same channel would force
every WS consumer to learn a binary sub-protocol. Keeping TTS as its
own `POST` lets the client request audio when (and only when) it
wants it, and lets non-copilot surfaces use the same endpoint.
"""

from __future__ import annotations

import secrets
from collections.abc import AsyncIterator

import structlog
from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import StreamingResponse

from app.middleware import get_request_id
from app.schemas.moderation import ModerationCheckRequest
from app.schemas.tts import TTSSynthesizeRequest
from app.services.auth import CurrentUser, require_adult
from app.services.moderation import (
    ModerationBackendError,
    ModerationService,
    get_moderation_service,
)
from app.tts import (
    TTSAudioFormat,
    TTSAuthError,
    TTSError,
    TTSRouter,
    TTSTimeoutError,
    TTSUpstreamError,
    get_tts_router,
)

logger = structlog.get_logger(__name__)

router = APIRouter(prefix="/tts", tags=["tts"])

# Verdicts that block TTS synthesis outright. `warn` proceeds — the
# coach hint pipeline upstream already tolerates a `warn` because the
# text can still be surfaced; TTS just speaks it.
_BLOCKING_VERDICTS = frozenset({"block", "redirect"})

# Container -> HTTP content-type. Aligned with `TTSAudioFormat`. wxapp
# + browser players both pick the right codec from the content-type
# header, so getting this mapping right is the only client-visible
# format contract.
_CONTENT_TYPE_BY_FORMAT: dict[TTSAudioFormat, str] = {
    "mp3": "audio/mpeg",
    "ogg": "audio/ogg",
    "wav": "audio/wav",
}


@router.post(
    "/synthesize",
    summary="Synthesize Mandarin text to streaming audio (PRD US-B2)",
    responses={
        200: {
            "content": {
                "audio/mpeg": {"schema": {"type": "string", "format": "binary"}},
                "audio/ogg": {"schema": {"type": "string", "format": "binary"}},
                "audio/wav": {"schema": {"type": "string", "format": "binary"}},
            },
            "description": (
                "Audio bytes streamed in the requested container. "
                "First byte typically arrives < 1.5s; total duration "
                "scales with text length."
            ),
        },
        401: {"description": "Missing or invalid bearer token."},
        403: {
            "description": (
                "`AGE_REQUIRED` if the JWT lacks `age_set=true`; "
                "`MINOR_FORBIDDEN` if the caller is under 18. PRD §1.5 / R-15."
            ),
        },
        422: {
            "description": (
                "`TTS_INPUT_BLOCKED` if the text fails red-line moderation "
                "(PRD §3.0.5); validation errors otherwise."
            ),
        },
        503: {
            "description": (
                "`TTS_UNAVAILABLE` when the configured provider(s) cannot "
                "synthesize — vendor outage, auth misconfig, or timeout."
            ),
        },
    },
)
async def synthesize_audio(
    payload: TTSSynthesizeRequest,
    request: Request,
    current: CurrentUser = Depends(require_adult),
    moderation: ModerationService = Depends(get_moderation_service),
    tts: TTSRouter = Depends(get_tts_router),
) -> StreamingResponse:
    """Moderate the input, then stream synthesized audio."""
    trace_id = get_request_id(request) or "tts_" + secrets.token_hex(8)

    await _check_moderation_or_raise(
        text=payload.text,
        moderation=moderation,
        user_id=current.user_id,
        is_minor=current.is_minor,
        trace_id=trace_id,
    )

    content_type = _CONTENT_TYPE_BY_FORMAT[payload.audio_format]
    return StreamingResponse(
        _safe_audio_stream(
            tts=tts,
            text=payload.text,
            voice=payload.voice,
            audio_format=payload.audio_format,
            user_id=current.user_id,
            trace_id=trace_id,
        ),
        media_type=content_type,
        headers={"X-Trace-Id": trace_id},
    )


async def _check_moderation_or_raise(
    *,
    text: str,
    moderation: ModerationService,
    user_id: str,
    is_minor: bool,
    trace_id: str,
) -> None:
    """Run red-line moderation against `text`. Block/redirect verdicts
    raise 422; backend outages raise 503 (TTS is non-essential UX, so
    failing closed on missing moderation is safer than synthesizing
    unscored content)."""
    try:
        decision = await moderation.check(
            ModerationCheckRequest(content=text, context="ai_output"),
            user_id=user_id,
            is_minor=is_minor,
            trace_id=trace_id,
        )
    except ModerationBackendError as exc:
        logger.warning(
            "tts_moderation_unavailable",
            user_id=user_id,
            trace_id=trace_id,
            backend=exc.backend,
            error=str(exc),
        )
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "code": "TTS_UNAVAILABLE",
                "message": "TTS moderation backend unavailable; please retry.",
            },
        ) from exc

    if decision.verdict in _BLOCKING_VERDICTS:
        logger.info(
            "tts_input_blocked",
            user_id=user_id,
            trace_id=trace_id,
            verdict=decision.verdict,
            categories=decision.categories,
        )
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={
                "code": "TTS_INPUT_BLOCKED",
                "message": "提交内容未通过审核，无法合成。",
                "verdict": decision.verdict,
            },
        )


async def _safe_audio_stream(
    *,
    tts: TTSRouter,
    text: str,
    voice: str,
    audio_format: TTSAudioFormat,
    user_id: str,
    trace_id: str,
) -> AsyncIterator[bytes]:
    """Wrap the TTS router's audio stream so vendor errors surface as
    503-shaped events instead of leaking raw stack traces over the
    response body.

    Because we're already inside a `StreamingResponse` by the time we
    iterate, we can't `raise HTTPException(503)` cleanly — the response
    headers (status + content-type) are already on the wire. Instead we
    log the typed error, end the audio stream early, and let the client
    handle the truncated body. The trace id in `X-Trace-Id` lets ops
    correlate a truncated body with the structured log row.
    """
    try:
        async for chunk in tts.synthesize(
            text,
            voice=voice,  # type: ignore[arg-type]
            audio_format=audio_format,
        ):
            if chunk.audio:
                yield chunk.audio
            if chunk.is_final:
                return
    except TTSAuthError as exc:
        logger.error(
            "tts_auth_failed",
            user_id=user_id,
            trace_id=trace_id,
            provider=exc.provider,
            error=str(exc),
        )
    except TTSTimeoutError as exc:
        logger.warning(
            "tts_timeout",
            user_id=user_id,
            trace_id=trace_id,
            provider=exc.provider,
            error=str(exc),
        )
    except TTSUpstreamError as exc:
        logger.warning(
            "tts_upstream_failed",
            user_id=user_id,
            trace_id=trace_id,
            provider=exc.provider,
            status_code=exc.status_code,
            error=str(exc),
        )
    except TTSError as exc:
        logger.warning(
            "tts_generic_failed",
            user_id=user_id,
            trace_id=trace_id,
            provider=exc.provider,
            error=str(exc),
        )
