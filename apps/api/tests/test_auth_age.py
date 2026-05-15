"""Unit tests for `compute_is_minor` + `birthdate_from_year`.

These are pure functions — no I/O — so we cover every branch that
matters for the minor gate (PRD §3.0.5 C):

  * unknown birthdate → False (don't gate by default; future PR adds
    the compulsory age-gate)
  * exactly turning 18 today → adult
  * birthday hasn't happened yet this calendar year → still minor
  * birthday already passed this calendar year → adult
  * trivially under (16) and trivially over (25)

The mid-year proxy used by `birthdate_from_year` is also pinned so a
future tightening shows up as a test failure rather than a silent
shift in who counts as a minor.
"""

from __future__ import annotations

from datetime import date

import pytest
from app.services.auth.age import (
    MINOR_AGE_THRESHOLD,
    birthdate_from_year,
    compute_is_minor,
)


def test_threshold_is_18_per_prd() -> None:
    # If this changes, every other test in the file shifts — fail
    # loudly so the PRD bump is conscious.
    assert MINOR_AGE_THRESHOLD == 18


def test_unknown_birthdate_defaults_to_adult() -> None:
    """`None` means "we haven't asked yet" — don't gate by default.
    A compulsory age-gate at login is a separate PR."""
    assert compute_is_minor(None, today=date(2026, 5, 15)) is False


def test_exactly_18_today_is_adult() -> None:
    """`age < 18` is the rule — a user whose 18th birthday is today
    counts as an adult."""
    today = date(2026, 5, 15)
    eighteen_today = date(2008, 5, 15)
    assert compute_is_minor(eighteen_today, today=today) is False


def test_birthday_later_this_year_still_minor() -> None:
    """A user who will turn 18 later this year is still a minor today."""
    today = date(2026, 5, 15)
    turns_18_in_october = date(2008, 10, 1)
    assert compute_is_minor(turns_18_in_october, today=today) is True


def test_birthday_earlier_this_year_is_adult() -> None:
    """A user who already turned 18 this calendar year is an adult."""
    today = date(2026, 5, 15)
    turned_18_in_january = date(2008, 1, 5)
    assert compute_is_minor(turned_18_in_january, today=today) is False


def test_age_14_is_minor() -> None:
    today = date(2026, 5, 15)
    assert compute_is_minor(date(2012, 1, 1), today=today) is True


def test_age_25_is_adult() -> None:
    today = date(2026, 5, 15)
    assert compute_is_minor(date(2001, 1, 1), today=today) is False


@pytest.mark.parametrize(
    ("today", "birthdate", "expected"),
    [
        # Leap-day birthday — Feb 29 user on a non-leap year. The
        # tuple comparison treats (3, 1) > (2, 29) so the user counts
        # as having "had their birthday" on Mar 1 of non-leap years.
        # That's a 24-hour discrepancy for one user every four years,
        # acceptable for a `< 18` gate.
        (date(2026, 3, 1), date(2008, 2, 29), False),
        (date(2026, 2, 28), date(2008, 2, 29), True),
    ],
)
def test_leap_day_birthday(today: date, birthdate: date, expected: bool) -> None:
    assert compute_is_minor(birthdate, today=today) is expected


def test_birthdate_from_year_proxy_is_stable() -> None:
    """The proxy used to materialise a bare `birth_year` is mid-year
    (Jul 1). Pinned because future shifts would silently change who
    counts as a minor for users whose birthdays sit on the boundary."""
    result = birthdate_from_year(2008)
    assert result == date(2008, 7, 1)


def test_birth_year_2008_today_2026_is_adult() -> None:
    """End-to-end: someone born in 2008 (via year-only collection) is
    17 on Jul 1, 2026 -> minor on most of 2026, adult after Jul 1."""
    bd = birthdate_from_year(2008)
    assert compute_is_minor(bd, today=date(2026, 6, 30)) is True
    assert compute_is_minor(bd, today=date(2026, 7, 1)) is False
