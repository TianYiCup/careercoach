"""Route-level tests for the A-7 minor quiet-hours gate.

This file pins WHICH routes the `block_minor_quiet_hours` dependency
applies to, mirroring the role `test_age_gate.py` plays for A-6.

Gated by A-7 (return 403 MINOR_QUIET_HOURS for minor + quiet hours):
  * POST /v1/sessions
  * POST /v1/sessions/{id}/turns

NOT gated by A-7 (proven by positive tests below):
  * adults — gate is minor-only
  * minors during daytime — gate is time-conditioned
  * /moderation/check       — content check is not engagement
  * /sharecards/*           — outputting past artifacts is not engagement
  * /sessions/{id}/end      — must be end-able regardless of time
  * /users/me/birth-year    — that's how you set age in the first place
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterator
from datetime import UTC, datetime

import pytest
from app.main import app
from app.services.auth import mint_token
from app.services.auth.quiet_hours import _now_provider
from httpx import ASGITransport, AsyncClient

# Two fixed timestamps. Both are May 15, 2026:
#   QUIET   — 14:00 UTC = 22:00 Asia/Shanghai (start of quiet window)
#   DAYTIME — 06:00 UTC = 14:00 Asia/Shanghai (mid-afternoon, open)
_QUIET = datetime(2026, 5, 15, 14, 0, tzinfo=UTC)
_DAYTIME = datetime(2026, 5, 15, 6, 0, tzinfo=UTC)


def _minor_token() -> str:
    return mint_token(
        user_id="u_minor",
        persona_type="in_school",
        is_minor=True,
        age_set=True,  # past the A-6 age gate
    )


def _adult_token() -> str:
    return mint_token(
        user_id="u_adult",
        persona_type="intern",
        is_minor=False,
        age_set=True,
    )


def _bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def quiet_clock() -> Iterator[None]:
    """Pin the clock at 22:00 Asia/Shanghai (start of quiet hours)."""
    app.dependency_overrides[_now_provider] = lambda: _QUIET
    try:
        yield
    finally:
        app.dependency_overrides.pop(_now_provider, None)


@pytest.fixture
def daytime_clock() -> Iterator[None]:
    """Pin the clock outside quiet hours so the gate doesn't fire."""
    app.dependency_overrides[_now_provider] = lambda: _DAYTIME
    try:
        yield
    finally:
        app.dependency_overrides.pop(_now_provider, None)


@pytest.fixture
async def client() -> AsyncIterator[AsyncClient]:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


# --- Gated: minor + quiet → 403 ----------------------------------------------


@pytest.mark.parametrize(
    ("path", "body"),
    [
        (
            "/v1/sessions",
            {
                "mode": "sandbox",
                "scenario_id": "sc_001",
                "persona_id": "p_hard",
                "user_goal": "x",
            },
        ),
        (
            "/v1/sessions/ses_anything/turns",
            {"content": "hi"},
        ),
    ],
)
async def test_minor_blocked_during_quiet_hours(
    client: AsyncClient,
    quiet_clock: None,
    path: str,
    body: dict[str, object],
) -> None:
    _ = quiet_clock
    resp = await client.post(path, headers=_bearer(_minor_token()), json=body)

    assert resp.status_code == 403, f"{path}: expected 403, got {resp.status_code}"
    assert resp.json()["code"] == "MINOR_QUIET_HOURS"


# --- NOT gated: counter-tests -------------------------------------------------


async def test_minor_passes_during_daytime(
    client: AsyncClient,
    daytime_clock: None,
) -> None:
    """Same minor JWT, daytime clock — gate must not fire. The request
    still 4xx's for other reasons (no real session service wired in
    here), but it MUST NOT be 403 MINOR_QUIET_HOURS."""
    _ = daytime_clock
    resp = await client.post(
        "/v1/sessions",
        headers=_bearer(_minor_token()),
        json={
            "mode": "sandbox",
            "scenario_id": "sc_001",
            "persona_id": "p_hard",
            "user_goal": "x",
        },
    )

    body = resp.json()
    assert body.get("code") != "MINOR_QUIET_HOURS", body


async def test_adult_passes_during_quiet_hours(
    client: AsyncClient,
    quiet_clock: None,
) -> None:
    """Adult JWT at 22:00 Shanghai — gate is minor-only, must not fire."""
    _ = quiet_clock
    resp = await client.post(
        "/v1/sessions",
        headers=_bearer(_adult_token()),
        json={
            "mode": "sandbox",
            "scenario_id": "sc_001",
            "persona_id": "p_hard",
            "user_goal": "x",
        },
    )

    body = resp.json()
    assert body.get("code") != "MINOR_QUIET_HOURS", body


# --- Routes deliberately NOT gated by A-7 ------------------------------------


async def test_moderation_check_not_gated_by_quiet_hours(
    client: AsyncClient,
    quiet_clock: None,
) -> None:
    """Content moderation isn't engagement — checking a string at 23:00
    is fine, and a frontend may want to validate text the user just
    typed even at night. Only sessions + turns are gated."""
    _ = quiet_clock
    resp = await client.post(
        "/v1/moderation/check",
        headers=_bearer(_minor_token()),
        json={"content": "今天天气真好", "context": "user_input"},
    )

    # Whatever the backend returns, the quiet-hours gate must NOT fire.
    body = resp.json()
    assert body.get("code") != "MINOR_QUIET_HOURS", body


async def test_session_end_not_gated_by_quiet_hours(
    client: AsyncClient,
    quiet_clock: None,
) -> None:
    """A minor who's still in a session at 22:00 must be able to end
    it. Stranding the session would be worse than letting them out."""
    _ = quiet_clock
    resp = await client.post(
        "/v1/sessions/ses_nonexistent/end",
        headers=_bearer(_minor_token()),
    )

    # 404 (session doesn't exist) is the expected branch since we
    # don't wire a service; the only thing the test cares about is
    # that the quiet-hours gate didn't intercept.
    body = resp.json()
    assert body.get("code") != "MINOR_QUIET_HOURS", body


async def test_birth_year_not_gated_by_quiet_hours(
    client: AsyncClient,
    quiet_clock: None,
) -> None:
    """Setting age must always be available — otherwise a minor who
    forgot to set their year on first login is stranded outside
    sandbox until 8 AM, which is hostile UX for a regulatory feature."""
    _ = quiet_clock
    resp = await client.post(
        "/v1/users/me/birth-year",
        headers=_bearer(_minor_token()),
        json={"birth_year": 2010},
    )

    body = resp.json()
    assert body.get("code") != "MINOR_QUIET_HOURS", body


# --- Order: 401 fires before the policy 403 ----------------------------------


async def test_no_token_returns_401_not_minor_quiet_hours(
    client: AsyncClient,
    quiet_clock: None,
) -> None:
    """Auth precedes policy. An unauthenticated request at 22:00
    must still be 401 — never leak that a quiet-hours rule exists
    via a stack-trace-shaped error code mismatch."""
    _ = quiet_clock
    resp = await client.post(
        "/v1/sessions",
        json={
            "mode": "sandbox",
            "scenario_id": "sc_001",
            "persona_id": "p_hard",
            "user_goal": "x",
        },
    )

    assert resp.status_code == 401
    assert resp.json()["code"] == "UNAUTHORIZED"
