"""Tests for `RequestIdMiddleware`.

Covers four branches the route layer cares about:
  1. Inbound X-Request-Id (well-formed) is honoured + echoed.
  2. Missing header → uuid is minted + echoed.
  3. Malformed inbound header (whitespace, newline injection, too short)
     is REJECTED — middleware mints a fresh uuid instead of trusting it.
  4. The same request_id surfaces to handlers AND to error responses,
     so a 4xx trace_id matches the response header.
"""

from __future__ import annotations

import re
from collections.abc import AsyncIterator

import pytest
from app.middleware import (
    REQUEST_ID_HEADER,
    RequestIdMiddleware,
    get_request_id,
)
from fastapi import FastAPI, HTTPException, Request
from httpx import ASGITransport, AsyncClient

_UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")


@pytest.fixture
def app() -> FastAPI:
    """A minimal app that surfaces the resolved id back to the test."""
    test_app = FastAPI()
    test_app.add_middleware(RequestIdMiddleware)

    @test_app.get("/echo")
    async def echo(request: Request) -> dict[str, str]:
        return {"request_id": get_request_id(request)}

    @test_app.get("/raise")
    async def raise_() -> None:
        raise HTTPException(status_code=400, detail="bad")

    return test_app


@pytest.fixture
async def client(app: FastAPI) -> AsyncIterator[AsyncClient]:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


async def test_inbound_well_formed_header_is_passed_through(client: AsyncClient) -> None:
    resp = await client.get(
        "/echo",
        headers={REQUEST_ID_HEADER: "req_abc1234567890"},
    )

    assert resp.status_code == 200
    assert resp.json() == {"request_id": "req_abc1234567890"}
    assert resp.headers[REQUEST_ID_HEADER] == "req_abc1234567890"


async def test_missing_header_mints_a_uuid(client: AsyncClient) -> None:
    resp = await client.get("/echo")

    body = resp.json()
    assert _UUID_RE.fullmatch(body["request_id"])
    assert resp.headers[REQUEST_ID_HEADER] == body["request_id"]


async def test_too_short_inbound_header_is_rejected(client: AsyncClient) -> None:
    """The 8-character minimum keeps log greps unambiguous — short
    values like `null` / `x` could legitimately occur as accidental
    placeholders and shouldn't be trusted as ids."""
    for bad in ("x", "null", "1234567"):  # all < 8 chars
        resp = await client.get("/echo", headers={REQUEST_ID_HEADER: bad})
        body = resp.json()
        assert _UUID_RE.fullmatch(body["request_id"]), (
            f"too-short inbound id {bad!r} leaked through"
        )


async def test_inbound_header_with_invalid_chars_is_rejected(
    client: AsyncClient,
) -> None:
    """Whitespace + control chars can't ride into log lines."""
    for bad in ("has space inside", "newline\ninjection"):
        resp = await client.get("/echo", headers={REQUEST_ID_HEADER: bad})
        body = resp.json()
        assert _UUID_RE.fullmatch(body["request_id"]), (
            f"malformed inbound id {bad!r} leaked through"
        )


async def test_error_response_still_carries_request_id_header(
    client: AsyncClient,
) -> None:
    """Even when the handler raises, the middleware must echo the
    request id back so the client can correlate."""
    resp = await client.get(
        "/raise",
        headers={REQUEST_ID_HEADER: "req_a1b2c3d4e5f60718"},
    )

    assert resp.status_code == 400
    assert resp.headers[REQUEST_ID_HEADER] == "req_a1b2c3d4e5f60718"


async def test_two_requests_get_distinct_ids(client: AsyncClient) -> None:
    """Sanity — a fresh request gets a fresh id when none is provided."""
    first = await client.get("/echo")
    second = await client.get("/echo")
    assert first.json()["request_id"] != second.json()["request_id"]


async def test_overly_long_inbound_header_rejected(client: AsyncClient) -> None:
    """129 chars exceeds the `_VALID_INBOUND_PATTERN` upper bound."""
    resp = await client.get(
        "/echo",
        headers={REQUEST_ID_HEADER: "x" * 129},
    )
    body = resp.json()
    assert body["request_id"] != "x" * 129
    assert _UUID_RE.fullmatch(body["request_id"])
