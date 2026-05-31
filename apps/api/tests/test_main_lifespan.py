"""App lifespan wiring — shutdown drains the moderation audit tasks.

PR #196 made the moderation audit write fire-and-forget; the in-flight
tasks live on `ModerationService`. This test pins that the FastAPI
lifespan actually calls `aclose()` on shutdown so the last few audit
rows aren't dropped when the process exits.
"""

from __future__ import annotations

import app.main as main_module
import pytest
from fastapi import FastAPI


class _SpyModerationService:
    """Stand-in that records whether `aclose()` was awaited."""

    def __init__(self) -> None:
        self.aclose_calls = 0

    async def aclose(self) -> None:
        self.aclose_calls += 1


async def test_lifespan_calls_moderation_aclose_on_shutdown(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    spy = _SpyModerationService()
    monkeypatch.setattr(main_module, "get_moderation_service", lambda: spy)

    app = FastAPI()
    # Enter then exit the lifespan: aclose runs in the shutdown half
    # (after the `yield`), so it must NOT have fired on enter.
    async with main_module._lifespan(app):
        assert spy.aclose_calls == 0

    assert spy.aclose_calls == 1


async def test_create_app_registers_lifespan() -> None:
    """The real app must carry the lifespan — guards against someone
    dropping the `lifespan=` kwarg in a future FastAPI() refactor."""
    app = main_module.create_app()
    assert app.router.lifespan_context is not None
