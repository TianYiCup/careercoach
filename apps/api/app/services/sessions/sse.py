"""Server-Sent Events frame helpers.

The wire format is fixed by HTML5 SSE:

    event: <name>\n
    data: <JSON>\n
    \n

Frontend parsers (apps/web MSW contract test, native EventSource) all
key on the blank line between frames; missing it merges frames into
one and breaks the discriminated-union parsing on the receiver side.

This module is a single source of truth for the encoding so route
handlers don't reinvent it.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class SseFrame:
    """One SSE event. `event` is the type discriminator (e.g.
    `opponent.delta`); `data` is JSON-serialized into the `data:` line."""

    event: str
    data: dict[str, Any]


def encode_frame(frame: SseFrame) -> bytes:
    """Render `frame` as the bytes a `StreamingResponse` writes.

    `ensure_ascii=False` keeps Chinese readable on the wire; SSE itself
    is UTF-8 so this is safe.
    """
    payload = json.dumps(frame.data, ensure_ascii=False, separators=(",", ":"))
    return f"event: {frame.event}\ndata: {payload}\n\n".encode()
