"""Where rendered PNG bytes go after the renderer returns.

v0 ships `LocalFilesystemStorage` — fine for dev and the Sprint-0 demo
where everything runs on the same host. Production swaps in an S3 /
OSS implementation behind the same Protocol; the service layer never
sees the difference.

Keys are restricted to `[A-Za-z0-9_-]+` with an optional single `/`
separator (e.g. `card_abc/p0_cover`). Wrapped writes one PNG per
page under a `<card_id>/p<index>_<slug>` key so the public URL matches
the contract the frontend mocks emit.
"""

from __future__ import annotations

import asyncio
import re
from pathlib import Path
from typing import Protocol, runtime_checkable

from app.services.sharecards.renderer import ShareCardRendererError

_PNG_CONTENT_TYPE = "image/png"

# `key` or `parent/child`, where each segment is alphanumeric with
# `_` or `-`. No leading `.`, no slashes-of-slashes, no traversal.
_KEY_PATTERN = re.compile(r"^[A-Za-z0-9_\-]+(/[A-Za-z0-9_\-]+)?$")


@runtime_checkable
class ShareCardStorage(Protocol):
    """Persists rendered PNG bytes and hands back a public URL."""

    name: str

    async def put(
        self,
        key: str,
        data: bytes,
        *,
        content_type: str = _PNG_CONTENT_TYPE,
    ) -> str:
        """Write `data` under `key`. Return the URL the client should hit."""
        ...

    def url_for(self, key: str) -> str:
        """Rebuild the public URL for an existing key without re-uploading."""
        ...


class LocalFilesystemStorage:
    """Writes PNGs to a local directory and exposes them under a fixed URL prefix.

    For local dev, set `public_base_url=http://localhost:8000/static/sharecards`
    and add a matching `StaticFiles` mount in `app.main` (v1). For CI /
    tests we point the storage at a tmp dir and never serve the files —
    the URL string is still asserted on shape.
    """

    name: str = "local_fs"

    def __init__(self, *, root: Path, public_base_url: str) -> None:
        if not public_base_url:
            raise ValueError("public_base_url must not be empty")
        self._root = root
        self._public_base_url = public_base_url.rstrip("/")
        self._root.mkdir(parents=True, exist_ok=True)

    async def put(
        self,
        key: str,
        data: bytes,
        *,
        content_type: str = _PNG_CONTENT_TYPE,
    ) -> str:
        _ = content_type  # local FS doesn't track MIME; S3 impl will use this
        if not key:
            raise ShareCardRendererError("storage key must not be empty", renderer=self.name)
        if not _KEY_PATTERN.fullmatch(key):
            # Defensive — guard against traversal in case caller passes
            # raw user input. card_ids are server-generated so this is
            # belt-and-braces, not the primary defence.
            raise ShareCardRendererError(
                f"storage key has unsafe characters: {key!r}",
                renderer=self.name,
            )

        path = self._root / f"{key}.png"
        # Create the subdirectory if the key encodes one (wrapped pages).
        await asyncio.to_thread(path.parent.mkdir, parents=True, exist_ok=True)
        await asyncio.to_thread(path.write_bytes, data)
        return self.url_for(key)

    def url_for(self, key: str) -> str:
        return f"{self._public_base_url}/{key}.png"
