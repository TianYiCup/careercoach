"""Age computation for the minor-mode gate (PRD §3.0.5 C / §1.5).

`is_minor` is a derived property: True iff the user is under 18 on the
day we check. We compute it from `birthdate` rather than storing the
raw age, so the value stays correct as years roll over without a
cron-job to flip rows.

For the v0.1 minor-gate MVP we only collect `birth_year` from the
client (minimum PII — exact day-of-birth isn't needed to decide
< 18 / ≥ 18). The repository materialises that as `date(year, 7, 1)` —
mid-year so the resulting age is correct on average regardless of the
real birth month, and the off-by-one error window is at most ~6 months
(acceptable for the gate's purpose; a stricter check would just ask
the user for full DOB and re-decide).
"""

from __future__ import annotations

from datetime import date

# Hard policy threshold — under this is_minor=True. Surfaced as a
# named constant so tests pin it explicitly; future tightening
# (e.g. PRD bumps to 16 for some sub-feature) updates this one place.
MINOR_AGE_THRESHOLD = 18

# Mid-year proxy when only `birth_year` is collected — see module
# docstring for the rationale.
_BIRTH_MONTH_PROXY = 7
_BIRTH_DAY_PROXY = 1


def birthdate_from_year(birth_year: int) -> date:
    """Project a bare `birth_year` to a `date` for storage. Uses a
    fixed mid-year proxy so any age computed from this value is
    correct on average across the population, regardless of the user's
    real birth month.
    """
    return date(birth_year, _BIRTH_MONTH_PROXY, _BIRTH_DAY_PROXY)


def compute_is_minor(birthdate: date | None, today: date) -> bool:
    """Return True iff `today - birthdate < 18 years`.

    `birthdate=None` means "unknown" — we default to False (treat as
    adult) because requiring age before any use would break onboarding.
    A future PR adds a compulsory age gate before sensitive features.
    The conservative default for *unknown* is acceptable here because:
      * the moderation strict-tier is a strengthening, not a relaxation
      * the `require_adult` route gate denies on True, not False — so
        unknown ages don't get a special bypass
    """
    if birthdate is None:
        return False
    age = today.year - birthdate.year
    # Subtract one if the birthday hasn't happened this calendar year
    # yet — `(month, day)` tuple comparison handles month + day in one
    # shot, no need to special-case Feb 29.
    if (today.month, today.day) < (birthdate.month, birthdate.day):
        age -= 1
    return age < MINOR_AGE_THRESHOLD


__all__ = ["MINOR_AGE_THRESHOLD", "birthdate_from_year", "compute_is_minor"]
