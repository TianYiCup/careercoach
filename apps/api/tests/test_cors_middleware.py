"""CORS preflight + simple-request tests.

The browser is the actual CORS enforcer; the server's job is to emit
the right `Access-Control-*` headers so the browser permits the
cross-origin request. These tests pin those headers without spinning
up a real browser.

Two header contracts under test:
  1. Preflight (OPTIONS) from an allowed origin → reflects the
     origin in `Access-Control-Allow-Origin` and lists the methods
     + headers we promised.
  2. Simple POST from an allowed origin → response carries
     `Access-Control-Allow-Origin` so the browser hands the body to
     JS instead of swallowing it.

Disallowed origins must NOT receive the reflected header — Starlette's
CORSMiddleware enforces this by simply not adding the header.

`X-Request-Id` is in `expose_headers` so the frontend can read it off
the response (correlate with backend logs); this is the
post-`RequestIdMiddleware` story for clients.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest
from app.main import app
from httpx import ASGITransport, AsyncClient


@pytest.fixture
async def client() -> AsyncIterator[AsyncClient]:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


async def test_preflight_from_allowed_origin_succeeds(client: AsyncClient) -> None:
    """Browser sends OPTIONS before a non-simple cross-origin POST."""
    resp = await client.options(
        "/v1/auth/sms/send",
        headers={
            "Origin": "http://localhost:5173",
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "Content-Type, Authorization",
        },
    )

    assert resp.status_code == 200
    assert resp.headers.get("access-control-allow-origin") == "http://localhost:5173"
    allow_methods = resp.headers.get("access-control-allow-methods", "")
    assert "POST" in allow_methods
    allow_headers = resp.headers.get("access-control-allow-headers", "").lower()
    assert "authorization" in allow_headers
    assert "content-type" in allow_headers


async def test_preflight_from_disallowed_origin_omits_cors_header(
    client: AsyncClient,
) -> None:
    """Origin not in the allowlist → no echo header → browser blocks."""
    resp = await client.options(
        "/v1/auth/sms/send",
        headers={
            "Origin": "https://evil.example.test",
            "Access-Control-Request-Method": "POST",
        },
    )
    # Starlette returns 400 or omits the allow header; either way the
    # browser refuses the cross-origin call. The strong invariant is
    # NO reflected origin.
    assert resp.headers.get("access-control-allow-origin") != "https://evil.example.test"


async def test_simple_request_from_allowed_origin_gets_cors_header(
    client: AsyncClient,
) -> None:
    """For non-preflight requests the response itself must still carry
    `Access-Control-Allow-Origin` or the browser hides the body from JS."""
    resp = await client.get(
        "/health",
        headers={"Origin": "http://localhost:5173"},
    )

    assert resp.status_code == 200
    assert resp.headers.get("access-control-allow-origin") == "http://localhost:5173"


async def test_tauri_origin_is_allowed(client: AsyncClient) -> None:
    """EXE shipping uses Tauri's webview origin — pin it in the
    default allowlist so dev runs of the desktop app aren't broken."""
    resp = await client.options(
        "/health",
        headers={
            "Origin": "tauri://localhost",
            "Access-Control-Request-Method": "GET",
        },
    )
    assert resp.headers.get("access-control-allow-origin") == "tauri://localhost"


async def test_request_id_header_is_exposed_via_cors(client: AsyncClient) -> None:
    """Frontend needs to read X-Request-Id off the response to grep
    backend logs by trace_id. CORS-by-default hides response headers
    from JS unless explicitly exposed."""
    resp = await client.get(
        "/health",
        headers={"Origin": "http://localhost:5173"},
    )

    exposed = resp.headers.get("access-control-expose-headers", "").lower()
    assert "x-request-id" in exposed


async def test_credentials_are_allowed_for_jwt(client: AsyncClient) -> None:
    """When hard-auth flips on and the frontend ships Bearer headers,
    `Access-Control-Allow-Credentials: true` is what lets the browser
    actually send them across origins."""
    resp = await client.options(
        "/v1/sessions",
        headers={
            "Origin": "http://localhost:5173",
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "Authorization",
        },
    )
    assert resp.headers.get("access-control-allow-credentials") == "true"
