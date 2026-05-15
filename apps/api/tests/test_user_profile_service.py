"""Service-level tests for the birth-year / minor-gate flow.

Pins how `AuthService.update_birth_year` integrates with the repo +
JWT minter:

  * a 16-year-old's birth year flips `is_minor=True` on the user row
    AND in the freshly-minted JWT
  * a 25-year-old stays adult
  * unknown user_id raises `ProfileUserNotFoundError` (route → 404)
  * repeated calls overwrite — last write wins (no append-only audit)
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from app.services.auth.code_store import InMemoryCodeStore
from app.services.auth.jwt_tokens import decode_token
from app.services.auth.rate_limit import InMemoryRateLimiter
from app.services.auth.service import (
    AuthService,
    LoggingDispatcher,
    ProfileUserNotFoundError,
)
from app.services.auth.user_repository import InMemoryUserRepository


def _service() -> tuple[AuthService, InMemoryUserRepository]:
    repo = InMemoryUserRepository()
    svc = AuthService(
        code_store=InMemoryCodeStore(),
        user_repo=repo,
        dispatcher=LoggingDispatcher(),
        rate_limiter=InMemoryRateLimiter(),
    )
    return svc, repo


async def _seed_user(repo: InMemoryUserRepository) -> str:
    record = await repo.create(
        phone="13800138000",
        nickname="K 学员 8000",
        persona_type="in_school",
        is_minor=False,
    )
    return record.user_id


async def test_update_birth_year_flips_minor_for_underage_user() -> None:
    svc, repo = _service()
    user_id = await _seed_user(repo)
    pinned_today = datetime(2026, 5, 15, tzinfo=UTC)

    resp = await svc.update_birth_year(user_id, birth_year=2012, today=pinned_today)

    # Public response carries the flipped flag …
    assert resp.user.is_minor is True
    # … the JWT carries it (so the route gate doesn't need a DB round-trip) …
    payload = decode_token(resp.token)
    assert payload is not None
    assert payload.is_minor is True
    # … and the repo row is the source of truth.
    stored = await repo.get_by_user_id(user_id)
    assert stored is not None
    assert stored.is_minor is True
    assert stored.birthdate is not None
    assert stored.birthdate.year == 2012


async def test_update_birth_year_keeps_adult_for_25_year_old() -> None:
    svc, repo = _service()
    user_id = await _seed_user(repo)
    pinned_today = datetime(2026, 5, 15, tzinfo=UTC)

    resp = await svc.update_birth_year(user_id, birth_year=2001, today=pinned_today)

    assert resp.user.is_minor is False
    payload = decode_token(resp.token)
    assert payload is not None
    assert payload.is_minor is False


async def test_update_birth_year_for_unknown_user_raises_not_found() -> None:
    """JWT subject doesn't map to a row (deleted account / stale token
    after DB wipe). Must surface as the route's 404, not a 500."""
    svc, _ = _service()

    with pytest.raises(ProfileUserNotFoundError):
        await svc.update_birth_year("u_does-not-exist", birth_year=2003)


async def test_update_birth_year_overwrites_prior_value() -> None:
    """A user who set the wrong year and corrects it should land on
    the second value, not an OR of the two."""
    svc, repo = _service()
    user_id = await _seed_user(repo)
    pinned_today = datetime(2026, 5, 15, tzinfo=UTC)

    # First set a minor year.
    await svc.update_birth_year(user_id, birth_year=2012, today=pinned_today)
    # Correct it to an adult year.
    resp = await svc.update_birth_year(user_id, birth_year=2001, today=pinned_today)

    assert resp.user.is_minor is False
    stored = await repo.get_by_user_id(user_id)
    assert stored is not None
    assert stored.birthdate is not None
    assert stored.birthdate.year == 2001
