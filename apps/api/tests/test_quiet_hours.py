"""Unit tests for `is_in_minor_quiet_hours` — the pure clock predicate
behind the A-7 minor quiet-hours gate (PRD §1.5).

Boundary behavior pinned:
  * 22:00:00 SH         → True  (start, closed)
  * 21:59:59 SH         → False (just before start)
  * 00:00:00 SH         → True  (midnight is inside the window)
  * 07:59:59 SH         → True  (one second before end)
  * 08:00:00 SH         → False (end, open — gate lifts)
  * 12:00:00 SH (noon)  → False
  * UTC inputs project to SH correctly (Asia/Shanghai = UTC+8 always)
"""

from __future__ import annotations

from datetime import UTC, datetime
from zoneinfo import ZoneInfo

from app.services.auth.quiet_hours import (
    QUIET_END_HOUR,
    QUIET_START_HOUR,
    is_in_minor_quiet_hours,
)

SH = ZoneInfo("Asia/Shanghai")


def test_constants_match_prd_policy() -> None:
    """PRD §1.5 pins the window. Future tightening updates these
    constants in one place; any quiet test below using the policy
    bounds inherits the change automatically."""
    assert QUIET_START_HOUR == 22
    assert QUIET_END_HOUR == 8


def test_2200_sharp_is_in_quiet_hours() -> None:
    assert is_in_minor_quiet_hours(datetime(2026, 5, 15, 22, 0, tzinfo=SH)) is True


def test_2159_is_not_in_quiet_hours() -> None:
    """The minute before quiet hours fire is still adult-tier."""
    assert is_in_minor_quiet_hours(datetime(2026, 5, 15, 21, 59, 59, tzinfo=SH)) is False


def test_midnight_is_in_quiet_hours() -> None:
    """The window straddles midnight; 00:00 is squarely inside."""
    assert is_in_minor_quiet_hours(datetime(2026, 5, 16, 0, 0, tzinfo=SH)) is True


def test_0759_is_in_quiet_hours() -> None:
    """One second before the gate lifts."""
    assert is_in_minor_quiet_hours(datetime(2026, 5, 16, 7, 59, 59, tzinfo=SH)) is True


def test_0800_sharp_is_not_in_quiet_hours() -> None:
    """Closed-at-start, open-at-end: 08:00 is the moment the gate
    lifts; matches what a user reads off "open 8am-10pm"."""
    assert is_in_minor_quiet_hours(datetime(2026, 5, 16, 8, 0, tzinfo=SH)) is False


def test_noon_is_not_in_quiet_hours() -> None:
    assert is_in_minor_quiet_hours(datetime(2026, 5, 15, 12, 0, tzinfo=SH)) is False


def test_utc_input_projects_to_shanghai() -> None:
    """A UTC input that's daytime UTC but quiet-hour Shanghai must
    return True — the function must respect the local tz, not the
    naive hour of the input."""
    # 16:00 UTC = 00:00 next day Asia/Shanghai (midnight, quiet)
    utc_midnight_in_shanghai = datetime(2026, 5, 15, 16, 0, tzinfo=UTC)
    assert is_in_minor_quiet_hours(utc_midnight_in_shanghai) is True

    # 00:00 UTC = 08:00 Asia/Shanghai (gate just lifted)
    utc_morning_in_shanghai = datetime(2026, 5, 15, 0, 0, tzinfo=UTC)
    assert is_in_minor_quiet_hours(utc_morning_in_shanghai) is False
