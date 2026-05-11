"""Small helper used by every node — collect an LLM stream to a string.

Nodes don't stream to the user directly; they need the full text
before they can return state. Centralising this avoids each node
re-implementing the accumulator (and forgetting to handle the
generator's `aclose`).
"""

from __future__ import annotations

import contextlib
from collections.abc import AsyncIterator


async def stream_to_text(stream: AsyncIterator[str]) -> str:
    chunks: list[str] = []
    try:
        async for chunk in stream:
            chunks.append(chunk)
    finally:
        aclose = getattr(stream, "aclose", None)
        if aclose is not None:
            with contextlib.suppress(Exception):
                await aclose()
    return "".join(chunks)
