"""A-41: `require_ops_token` dependency tests.

The dep gates `/v1/ops/*` endpoints with a static `X-Ops-Token`
header. These tests mount a tiny in-test FastAPI app with one
gated route so each branch (disabled / missing / wrong / ok) gets
an end-to-end assertion that the dep + FastAPI integration both
behave correctly — not just the function in isolation.

`get_settings.cache_clear()` is required around every monkeypatch
because `Settings` is `@lru_cache(maxsize=1)`-singleton'd and would
otherwise return the value cached by an earlier test run.
"""

from __future__ import annotations

import pytest
from app.config import get_settings
from app.services.ops import require_ops_token
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient


def _build_app() -> FastAPI:
    """Tiny app with one gated endpoint. Mirrors how A-42 will attach
    `require_ops_token` to the cost rollup route. Returning
    `{"ok": true}` on pass makes the success-path assertion concrete
    — a body proves the route handler actually executed (distinguishes
    "dep passed" from "dep raised but framework swallowed")."""
    app = FastAPI()

    @app.get("/v1/ops/probe")
    async def _probe(_: None = Depends(require_ops_token)) -> dict[str, bool]:
        return {"ok": True}

    return app


@pytest.fixture
def _client() -> TestClient:
    return TestClient(_build_app())


# --- disabled deployment ---


def test_503_when_ops_api_token_not_configured(
    monkeypatch: pytest.MonkeyPatch, _client: TestClient
) -> None:
    """Empty `OPS_API_TOKEN` env (the default) must fail-closed with
    503 OPS_DISABLED — never silently open access. A forgotten env
    var is the single most likely deployment mistake; making it loud
    is cheap insurance."""
    monkeypatch.setenv("OPS_API_TOKEN", "")
    get_settings.cache_clear()
    try:
        resp = _client.get("/v1/ops/probe", headers={"X-Ops-Token": "anything"})
        assert resp.status_code == 503
        body = resp.json()
        assert body["detail"]["code"] == "OPS_DISABLED"
    finally:
        get_settings.cache_clear()


def test_503_returned_regardless_of_header_value_when_disabled(
    monkeypatch: pytest.MonkeyPatch, _client: TestClient
) -> None:
    """The disabled-deployment 503 must NOT leak any signal about
    what the token might be — same response whether the header is
    absent, empty, or carries a guess. Otherwise an attacker can
    distinguish "endpoint disabled" from "endpoint exists, wrong
    token" via response shape."""
    monkeypatch.setenv("OPS_API_TOKEN", "")
    get_settings.cache_clear()
    try:
        # No header at all
        resp = _client.get("/v1/ops/probe")
        assert resp.status_code == 503
        assert resp.json()["detail"]["code"] == "OPS_DISABLED"
        # Header set to various wrong values — all must still 503
        for header_value in ("", "guess", "another-guess"):
            resp = _client.get("/v1/ops/probe", headers={"X-Ops-Token": header_value})
            assert resp.status_code == 503, f"header={header_value!r} broke parity"
            assert resp.json()["detail"]["code"] == "OPS_DISABLED"
    finally:
        get_settings.cache_clear()


# --- enabled but unauthorized ---


def test_401_when_header_missing(monkeypatch: pytest.MonkeyPatch, _client: TestClient) -> None:
    monkeypatch.setenv("OPS_API_TOKEN", "real-token")
    get_settings.cache_clear()
    try:
        resp = _client.get("/v1/ops/probe")
        assert resp.status_code == 401
        body = resp.json()
        assert body["detail"]["code"] == "OPS_AUTH_REQUIRED"
        assert resp.headers.get("WWW-Authenticate") == "Ops"
    finally:
        get_settings.cache_clear()


def test_401_when_header_value_wrong(monkeypatch: pytest.MonkeyPatch, _client: TestClient) -> None:
    monkeypatch.setenv("OPS_API_TOKEN", "real-token")
    get_settings.cache_clear()
    try:
        resp = _client.get("/v1/ops/probe", headers={"X-Ops-Token": "wrong"})
        assert resp.status_code == 401
        assert resp.json()["detail"]["code"] == "OPS_AUTH_REQUIRED"
    finally:
        get_settings.cache_clear()


def test_401_when_header_value_empty_string(
    monkeypatch: pytest.MonkeyPatch, _client: TestClient
) -> None:
    """`X-Ops-Token:` with an empty value must NOT match an empty
    server-side configured token (which would be unreachable here
    anyway — empty `ops_api_token` short-circuits to 503 first).
    But pin it explicitly: a future regression where someone
    "fixes" the disabled-branch could otherwise turn empty/empty
    into a silent allow."""
    monkeypatch.setenv("OPS_API_TOKEN", "real-token")
    get_settings.cache_clear()
    try:
        resp = _client.get("/v1/ops/probe", headers={"X-Ops-Token": ""})
        assert resp.status_code == 401
        assert resp.json()["detail"]["code"] == "OPS_AUTH_REQUIRED"
    finally:
        get_settings.cache_clear()


def test_same_error_code_for_missing_and_wrong_token(
    monkeypatch: pytest.MonkeyPatch, _client: TestClient
) -> None:
    """Single `OPS_AUTH_REQUIRED` code for both branches — an attacker
    must not be able to distinguish "you forgot the header" from
    "your guess was wrong". Pinning this against future drift where
    someone adds `OPS_TOKEN_MISSING` vs `OPS_TOKEN_INVALID`."""
    monkeypatch.setenv("OPS_API_TOKEN", "real-token")
    get_settings.cache_clear()
    try:
        missing = _client.get("/v1/ops/probe")
        wrong = _client.get("/v1/ops/probe", headers={"X-Ops-Token": "nope"})
        assert missing.status_code == 401
        assert wrong.status_code == 401
        assert missing.json()["detail"]["code"] == wrong.json()["detail"]["code"]
    finally:
        get_settings.cache_clear()


# --- enabled and authorized ---


def test_200_when_header_matches_configured_token(
    monkeypatch: pytest.MonkeyPatch, _client: TestClient
) -> None:
    monkeypatch.setenv("OPS_API_TOKEN", "real-token")
    get_settings.cache_clear()
    try:
        resp = _client.get("/v1/ops/probe", headers={"X-Ops-Token": "real-token"})
        assert resp.status_code == 200
        assert resp.json() == {"ok": True}
    finally:
        get_settings.cache_clear()


def test_match_is_case_sensitive(monkeypatch: pytest.MonkeyPatch, _client: TestClient) -> None:
    """`hmac.compare_digest` is byte-exact. Tokens are case-sensitive
    secrets, NOT case-insensitive identifiers — pinning this so a
    future refactor that swaps in `lower()` somewhere breaks here."""
    monkeypatch.setenv("OPS_API_TOKEN", "Real-Token-ABC")
    get_settings.cache_clear()
    try:
        # Exact match passes.
        ok = _client.get("/v1/ops/probe", headers={"X-Ops-Token": "Real-Token-ABC"})
        assert ok.status_code == 200

        # Different case fails.
        bad = _client.get("/v1/ops/probe", headers={"X-Ops-Token": "real-token-abc"})
        assert bad.status_code == 401
    finally:
        get_settings.cache_clear()


# --- dep usable as a function-level dependency, not just decorator ---


async def test_dependency_passes_on_match_when_called_directly(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Direct unit call (no FastAPI in the loop) confirms the dep
    doesn't raise on a valid token — and (via mypy's `-> None`
    return type) that route handlers can't accidentally rely on a
    return shape that a future refactor might change."""
    monkeypatch.setenv("OPS_API_TOKEN", "tok")
    get_settings.cache_clear()
    try:
        # Should not raise. No return value to inspect.
        await require_ops_token(x_ops_token="tok")
    finally:
        get_settings.cache_clear()
