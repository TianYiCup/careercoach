"""Minor quiet hours (PRD §1.5 防沉迷).

Block engagement endpoints (`POST /v1/sessions`, `POST /v1/sessions/
{id}/turns`) for users with `is_minor=True` during 22:00–08:00
Asia/Shanghai. The window straddles midnight so a single `hour ∈
[22, 24) ∪ [0, 8)` predicate covers both halves.

Why Asia/Shanghai, not the caller's local time:

  * the target audience is mainland China (PRD §1.1)
  * regulatory framing (网信办防沉迷指导) anchors on local CN time
  * picking a single tz keeps the audit story simple — every minor
    sees the same window, no per-user clock disputes

Why minor-only, not blanket:

  * adults aren't subject to anti-addiction rules
  * the gate would be hostile to working adults practicing for
    early-morning interviews or after-hours job hunts (PRD §1.1
    target persona)

The dependency chains AFTER `require_age_set` (compulsory age gate)
so the resolution order is: have-token → age-declared → not-in-quiet-
hours. Tokens that don't even know their age never hit this check.

Clock injection:

`_now_provider` is a FastAPI sub-dependency so tests can override it
to a fixed wall-clock without monkeypatching `datetime.now`. Pure
unit tests of `is_in_minor_quiet_hours` pass `now` directly.
"""

from __future__ import annotations

from datetime import UTC, datetime
from zoneinfo import ZoneInfo

# Asia/Shanghai is UTC+8 year-round (China abolished DST in 1991), so
# the predicate stays stable across the year — no twice-yearly
# off-by-one to worry about.
SHANGHAI_TZ = ZoneInfo("Asia/Shanghai")

# v0 policy values surfaced as constants here so the route layer and
# tests pin to the same numbers. Future tightening (e.g. 21:00 start)
# updates one place.
QUIET_START_HOUR = 22
QUIET_END_HOUR = 8


def is_in_minor_quiet_hours(now: datetime) -> bool:
    """True iff `now` (any tz) projects to Asia/Shanghai local time
    within [22:00, 08:00).

    Closed-at-start, open-at-end so 22:00:00 is blocked but 08:00:00
    is the moment the gate lifts — matches what users would read off
    a "the app is open 8am–10pm" sign. Sub-second precision is dropped
    by the hour comparison; we don't gate to-the-minute.
    """
    local_hour = now.astimezone(SHANGHAI_TZ).hour
    return local_hour >= QUIET_START_HOUR or local_hour < QUIET_END_HOUR


async def _now_provider() -> datetime:
    """FastAPI dependency that returns the current UTC instant.

    Tests override this on `app.dependency_overrides` to pin a fixed
    wall-clock. Production runs use the unwrapped `datetime.now(UTC)`.
    """
    return datetime.now(UTC)


__all__ = [
    "QUIET_END_HOUR",
    "QUIET_START_HOUR",
    "SHANGHAI_TZ",
    "_now_provider",
    "is_in_minor_quiet_hours",
]
