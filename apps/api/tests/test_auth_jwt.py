"""Tests for `app.services.auth.jwt_tokens`.

Mint + decode round-trips, plus the negative paths where decode_token
must return `None` rather than raise.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from app.services.auth.jwt_tokens import (
    TokenPayload,
    decode_token,
    mint_token,
)


def test_mint_then_decode_roundtrip_preserves_claims() -> None:
    token = mint_token(
        user_id="u_abc",
        persona_type="intern",
        is_minor=False,
    )
    payload = decode_token(token)

    assert isinstance(payload, TokenPayload)
    assert payload.user_id == "u_abc"
    assert payload.persona_type == "intern"
    assert payload.is_minor is False
    # Default TTL is 30 days; we should land well in the future but
    # not absurdly far out.
    delta = payload.expires_at - datetime.now(UTC)
    assert timedelta(days=29) < delta < timedelta(days=31)


def test_decode_token_returns_none_for_expired_token() -> None:
    """Expired tokens must NOT decode — security gate."""
    yesterday = datetime.now(UTC) - timedelta(days=2)
    token = mint_token(
        user_id="u_x",
        persona_type="in_school",
        is_minor=False,
        ttl=timedelta(days=1),
        now=yesterday,
    )

    assert decode_token(token) is None


def test_decode_token_returns_none_for_tampered_signature() -> None:
    token = mint_token(
        user_id="u_x",
        persona_type="in_school",
        is_minor=False,
    )
    # Flip a byte in the signature segment — last segment after the
    # second dot. Single-char swap keeps base64 shape valid but breaks
    # the signature, which is what we want to test.
    head, payload, sig = token.split(".")
    tampered = f"{head}.{payload}.{'a' if sig[0] != 'a' else 'b'}{sig[1:]}"

    assert decode_token(tampered) is None


def test_decode_token_returns_none_for_garbage_input() -> None:
    assert decode_token("not-a-jwt") is None
    assert decode_token("") is None


def test_is_minor_flag_round_trips_as_bool() -> None:
    minor_token = mint_token(
        user_id="u_minor",
        persona_type="in_school",
        is_minor=True,
    )
    payload = decode_token(minor_token)
    assert payload is not None
    assert payload.is_minor is True
