"""TTS (text-to-speech) API schemas — PRD US-B2.

`POST /v1/tts/synthesize` body. The response is binary audio bytes
(content-type per the requested `audio_format`), so no Pydantic
response model — see the route handler's `StreamingResponse`.

Field choices
-------------
* `text`         — required, capped at `MAX_TEXT_CHARACTERS`. The cap
                   matches `app.tts.provider.MAX_TEXT_CHARACTERS` so a
                   422 here is the only failure mode for over-long
                   input; the provider layer never sees an oversized
                   payload.
* `voice`        — stable id (currently only `"k-warm"`); the provider
                   maps it to a vendor voice name. v1 ships one voice
                   so a future addition is purely additive.
* `audio_format` — defaults to `mp3` because every browser + every
                   wxapp `<audio>` tag plays it.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from app.tts.provider import MAX_TEXT_CHARACTERS, TTSAudioFormat, TTSVoice


class TTSSynthesizeRequest(BaseModel):
    """`POST /v1/tts/synthesize` body."""

    text: str = Field(
        ...,
        min_length=1,
        max_length=MAX_TEXT_CHARACTERS,
        description=(
            "Mandarin text to synthesize. Subject to red-line moderation before "
            "the provider sees it (PRD §3.0.5)."
        ),
        examples=["下一句可以这样说：先问对方时间方便不方便，再切正题。"],
    )
    voice: TTSVoice = Field(
        default="k-warm",
        description=(
            "Stable voice id. v1 ships only `k-warm` (warm Mandarin female, "
            "matching Coach K's '嘴硬心软' tone). Future voices append here."
        ),
        examples=["k-warm"],
    )
    audio_format: TTSAudioFormat = Field(
        default="mp3",
        description="Container for the response body. `mp3` plays everywhere.",
        examples=["mp3"],
    )


# Re-exported for OpenAPI clients that prefer to import the type
# alongside the request body.
__all__ = ["TTSAudioFormat", "TTSSynthesizeRequest", "TTSVoice"]

# Keep Literal in the public symbol scope without `unused-import` noise
# — Pydantic re-uses it under the hood for the typed fields above.
_ = Literal
