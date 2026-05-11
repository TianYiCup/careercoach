"""Dump FastAPI's auto-generated OpenAPI spec to apps/api/openapi.yaml.

Run:    uv run python scripts/dump_openapi.py
CI:     check the file is up-to-date with `git diff --exit-code openapi.yaml`.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import yaml

REPO_API = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_API))

from app.main import app  # noqa: E402

OUTPUT = REPO_API / "openapi.yaml"


def _dump() -> None:
    spec = app.openapi()
    OUTPUT.write_text(
        yaml.safe_dump(
            json.loads(json.dumps(spec)),
            sort_keys=False,
            allow_unicode=True,
            width=100,
        ),
        encoding="utf-8",
    )
    print(f"wrote {OUTPUT.relative_to(REPO_API.parent.parent)}")


if __name__ == "__main__":
    _dump()
