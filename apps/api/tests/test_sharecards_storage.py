"""Local-filesystem storage tests for the share-card pipeline (PR ③).

These exercise file IO without touching the DB or the renderer — the
storage protocol is the seam between rendered bytes and the public URL,
so we pin its behaviour here.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from app.services.sharecards import LocalFilesystemStorage, ShareCardStorage
from app.services.sharecards.renderer import ShareCardRendererError


def _make_storage(tmp_path: Path) -> LocalFilesystemStorage:
    return LocalFilesystemStorage(
        root=tmp_path / "sharecards",
        public_base_url="http://localhost:8000/static/sharecards",
    )


def test_storage_implements_protocol(tmp_path: Path) -> None:
    storage = _make_storage(tmp_path)
    assert isinstance(storage, ShareCardStorage)
    assert storage.name == "local_fs"


def test_root_directory_is_created_on_init(tmp_path: Path) -> None:
    root = tmp_path / "nested" / "sharecards"
    assert not root.exists()
    LocalFilesystemStorage(root=root, public_base_url="http://x/y")
    assert root.is_dir()


def test_empty_public_base_url_rejected(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="public_base_url"):
        LocalFilesystemStorage(root=tmp_path, public_base_url="")


async def test_put_writes_png_and_returns_url(tmp_path: Path) -> None:
    storage = _make_storage(tmp_path)
    data = b"\x89PNG\r\n\x1a\n" + b"sample"

    url = await storage.put("card_abc", data)

    assert url == "http://localhost:8000/static/sharecards/card_abc.png"
    written = (tmp_path / "sharecards" / "card_abc.png").read_bytes()
    assert written == data


async def test_put_rejects_empty_key(tmp_path: Path) -> None:
    storage = _make_storage(tmp_path)
    with pytest.raises(ShareCardRendererError, match="storage key must not be empty"):
        await storage.put("", b"abc")


@pytest.mark.parametrize("bad_key", ["../escape", "sub/dir/card", ".hidden"])
async def test_put_rejects_traversal_or_dotfile_keys(tmp_path: Path, bad_key: str) -> None:
    storage = _make_storage(tmp_path)
    with pytest.raises(ShareCardRendererError, match="unsafe characters"):
        await storage.put(bad_key, b"abc")


def test_url_for_strips_trailing_slash_on_base(tmp_path: Path) -> None:
    storage = LocalFilesystemStorage(
        root=tmp_path,
        public_base_url="http://localhost:8000/static/sharecards/",
    )
    assert storage.url_for("card_x") == "http://localhost:8000/static/sharecards/card_x.png"
