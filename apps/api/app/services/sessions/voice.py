"""Voice-turn transcription — the ASR half of `POST /sessions/{id}/voice`.

PRD §7.4: a voice turn uploads a 16kHz wav/opus utterance; the server
transcribes it, emits a `user.transcribed` SSE frame, then runs the
*identical* opponent / coach / judge pipeline a typed turn runs. This
module owns only the transcription step — the route composes it with
`TurnService`, so a voice turn and a typed turn diverge for exactly one
ASR call and converge again.

The audio bytes are never persisted (CLAUDE.md constraint #2 — 不存语音);
only the transcript flows on, exactly like a typed reply.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

import structlog

from app.asr import ASRError, ASRProvider

logger = structlog.get_logger(__name__)


class VoiceTranscriptionError(RuntimeError):
    """ASR failed for this upload.

    The route maps this to 502 — voice is temporarily unavailable and
    the client should fall back to typing. End-side whisper.cpp
    degradation (PRD §3.2 US-A3 L3) is a later PR; until then an ASR
    outage is surfaced honestly rather than silently swallowed.
    """


class VoiceTranscriptionService:
    """Transcribes one uploaded utterance via an `ASRProvider`."""

    def __init__(self, *, asr: ASRProvider) -> None:
        self._asr = asr

    async def transcribe(self, audio: bytes) -> str:
        """Transcribe one uploaded utterance to its final transcript.

        The `ASRProvider` Protocol is streaming (audio-in, partial-then-
        final) to match real vendors; a file upload is the degenerate
        single-chunk case, so the whole blob is fed as one chunk and
        only the terminal `final` event's text is kept. An empty / all-
        whitespace result comes back as `""` — the caller (route) turns
        that into the PRD US-A3 "没听清，请重试" response.

        Raises `VoiceTranscriptionError` if the provider raises any
        `ASRError`; httpx / vendor errors never bubble past this layer.
        """

        async def _single_chunk() -> AsyncIterator[bytes]:
            yield audio

        final_text = ""
        try:
            async for event in self._asr.transcribe_stream(_single_chunk()):
                if event.kind == "final":
                    final_text = event.text
        except ASRError as exc:
            logger.warning(
                "voice_transcription_failed",
                provider=exc.provider,
                error=str(exc),
            )
            raise VoiceTranscriptionError(str(exc)) from exc
        return final_text.strip()


__all__ = ["VoiceTranscriptionError", "VoiceTranscriptionService"]
