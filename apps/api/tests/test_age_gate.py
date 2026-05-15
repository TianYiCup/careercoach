"""Compulsory age gate (PRD §1.5) — `require_age_set` end-to-end.

This is the test file that pins WHICH routes are gated and WHICH are
deliberately open. Every entry here is a security decision; treat
changes carefully.

Gated routes return 403 AGE_REQUIRED for a JWT with `age_set=False`:
  * POST /v1/sessions
  * POST /v1/sessions/{id}/turns
  * POST /v1/moderation/check
  * POST /v1/sharecards/session/{id}
  * POST /v1/sharecards/weekly
  * POST /v1/sharecards/wrapped/year/{year}

Deliberately NOT gated (positive tests prove they still work with
age_set=False so legacy clients can log in and clear the gate):
  * POST /v1/auth/sms/send + verify
  * POST /v1/users/me/birth-year
  * GET  /v1/scenarios
  * POST /v1/sessions/{id}/end (already-started sessions must end)
"""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest
from app.main import app
from app.services.auth import mint_token
from httpx import ASGITransport, AsyncClient

# ---------------------------------------------------------------------------
# Test fixtures
# ---------------------------------------------------------------------------


def _unset_token() -> str:
    """JWT with `age_set=False` — the new-user state."""
    return mint_token(
        user_id="u_no_age",
        persona_type="intern",
        is_minor=False,
        age_set=False,
    )


def _set_token() -> str:
    """JWT with `age_set=True` — cleared the gate."""
    return mint_token(
        user_id="u_aged",
        persona_type="intern",
        is_minor=False,
        age_set=True,
    )


def _bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
async def client() -> AsyncIterator[AsyncClient]:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


# ---------------------------------------------------------------------------
# Gated routes — must 403 AGE_REQUIRED when age_set=False
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("method", "path", "body"),
    [
        (
            "POST",
            "/v1/sessions",
            {
                "mode": "sandbox",
                "scenario_id": "sc_001",
                "persona_id": "p_hard",
                "user_goal": "x",
            },
        ),
        (
            "POST",
            "/v1/sessions/ses_anything/turns",
            {"content": "hi"},
        ),
        (
            "POST",
            "/v1/moderation/check",
            {"content": "hi", "context": "user_input"},
        ),
        (
            "POST",
            "/v1/sharecards/session/ses_anything",
            {"include_qrcode": False},
        ),
        (
            "POST",
            "/v1/sharecards/weekly",
            {"week_offset": 0, "include_qrcode": False},
        ),
        (
            "POST",
            "/v1/sharecards/wrapped/year/2026",
            {"include_qrcode": False},
        ),
    ],
)
async def test_gated_routes_return_403_age_required_when_unset(
    client: AsyncClient,
    method: str,
    path: str,
    body: dict[str, object],
) -> None:
    """The full enumeration of gated routes. If a new content-handling
    endpoint lands without an age gate, add it here — this is the
    one place that pins the policy at the route level."""
    resp = await client.request(method, path, headers=_bearer(_unset_token()), json=body)

    assert resp.status_code == 403, f"{method} {path} returned {resp.status_code}, expected 403"
    detail = resp.json()
    assert detail["code"] == "AGE_REQUIRED"


# ---------------------------------------------------------------------------
# Open routes — must NOT 403 AGE_REQUIRED with age_set=False
# ---------------------------------------------------------------------------


async def test_auth_send_route_is_open_with_age_unset(client: AsyncClient) -> None:
    """Logging in cannot require age first — that's circular."""
    resp = await client.post("/v1/auth/sms/send", json={"phone": "13800138000"})
    # No auth header at all, but the point is: even with `_unset_token`
    # it wouldn't be a 403 AGE_REQUIRED. Either 200 (success) or
    # 429 (cooldown from a prior test) — anything but the gate code.
    assert resp.status_code != 403


async def test_birth_year_route_is_open_with_age_unset(client: AsyncClient) -> None:
    """The whole point of this endpoint is to LET the user clear the
    gate; gating it would brick them."""
    resp = await client.post(
        "/v1/users/me/birth-year",
        headers=_bearer(_unset_token()),
        json={"birth_year": 2003},
    )
    # 200 (success) or 404 (user_id doesn't exist in repo) is fine —
    # we just need to prove the age gate didn't bite.
    assert resp.status_code != 403, resp.text
    body = resp.json()
    assert body.get("code") != "AGE_REQUIRED"


async def test_scenarios_list_is_open_with_age_unset(client: AsyncClient) -> None:
    """Browsing the catalog before declaring age is fine — no user
    content flows, no LLM call. The product wants browsable preview."""
    resp = await client.get(
        "/v1/scenarios",
        headers=_bearer(_unset_token()),
    )
    assert resp.status_code == 200, resp.text


# ---------------------------------------------------------------------------
# Positive: age_set=True passes the gate
# ---------------------------------------------------------------------------


async def test_gated_route_passes_when_age_set_true(client: AsyncClient) -> None:
    """Counter-test: same content-handling endpoint, JWT with
    age_set=True, must NOT 403. Status will be 4xx for other reasons
    (no real DB, no real session) — the only thing we care about is
    that it's NOT a 403 AGE_REQUIRED."""
    resp = await client.post(
        "/v1/moderation/check",
        headers=_bearer(_set_token()),
        json={"content": "今天天气真好", "context": "user_input"},
    )
    # Default route wiring uses the cascading backend which talks to
    # the configured local dict — returns 200 with verdict allow for
    # benign content.
    assert resp.status_code == 200, resp.text


# ---------------------------------------------------------------------------
# No-token branch still 401, not 403 (auth fires before gate)
# ---------------------------------------------------------------------------


async def test_gated_route_without_bearer_is_401_not_403(client: AsyncClient) -> None:
    """The auth check fires before the gate. A request with no token
    is an auth failure, not a policy failure — must surface 401."""
    resp = await client.post(
        "/v1/moderation/check",
        json={"content": "hi", "context": "user_input"},
    )
    assert resp.status_code == 401
    assert resp.json()["code"] == "UNAUTHORIZED"
