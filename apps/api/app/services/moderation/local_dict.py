"""`DictBackend` — keyword-based moderation against the bundled v0 dict.

The dict file lives next to this module as package data, so the
backend can be loaded from any CWD (dev, container, alembic, tests).
Override the path in tests via `DictBackend.from_text(...)`.

Verdict policy (PR ②):
  * any `self_harm` match wins, regardless of what else matches —
    we redirect to the crisis-line resource (PRD §3.0.5 A).
  * any other red-line category match → `verdict=block`.
  * nothing matched → `verdict=allow`.

A separate `warn` branch is deferred to PR ④ once the 200-sample
regression tells us which categories are FP-noisy enough to warrant
softening.
"""

from __future__ import annotations

from pathlib import Path
from typing import get_args

from app.schemas.moderation import (
    ModerationCategory,
    ModerationContext,
    RedirectResource,
)
from app.services.moderation.types import Decision

DEFAULT_DICT_PATH = Path(__file__).parent / "data" / "sensitive_dict_v0.txt"

# PRD §3.0.5 A — Beijing 24h crisis hotline, run by Huilongguan Hospital.
# Hard-coded for v0; v1 can localise per user region.
SELF_HARM_RESOURCE = RedirectResource(
    title="心理援助 24h 热线",
    url="tel:010-82951332",
)

# Confidence scores for v0 — deliberately not 1.0 so PR ③'s cloud
# backend can dominate on cascading agreement.
SELF_HARM_SCORE = 0.95
RED_LINE_SCORE = 0.85

_VALID_CATEGORIES: frozenset[str] = frozenset(get_args(ModerationCategory))


class DictBackend:
    """Substring-match against a curated red-line keyword list.

    The matcher is intentionally simple — `term in content` for ~200
    short strings on inputs ≤ 8000 chars is well under 2 ms per call,
    so Aho-Corasick / trie machinery isn't worth the dependency.

    Structurally implements `app.services.moderation.ModerationBackend`.
    """

    name: str = "local_dict"

    def __init__(self, terms: dict[str, ModerationCategory]) -> None:
        if not terms:
            raise ValueError("DictBackend requires at least one term")
        # Sort by descending length so longer phrases win the
        # category-priority tie-break naturally during debug logs.
        self._terms: tuple[tuple[str, ModerationCategory], ...] = tuple(
            sorted(terms.items(), key=lambda kv: -len(kv[0]))
        )

    @classmethod
    def from_file(cls, path: Path = DEFAULT_DICT_PATH) -> DictBackend:
        return cls.from_text(path.read_text(encoding="utf-8"))

    @classmethod
    def from_text(cls, text: str) -> DictBackend:
        return cls(_parse_dict(text))

    async def evaluate(self, content: str, context: ModerationContext) -> Decision:
        _ = context  # v0 dict doesn't vary by context yet; cloud backend will.

        categories: set[ModerationCategory] = set()
        for term, category in self._terms:
            if term in content:
                categories.add(category)

        if not categories:
            return Decision(verdict="allow", score=0.0)

        if "self_harm" in categories:
            return Decision(
                verdict="redirect",
                score=SELF_HARM_SCORE,
                categories=tuple(sorted(categories)),
                redirect_resource=SELF_HARM_RESOURCE,
            )

        return Decision(
            verdict="block",
            score=RED_LINE_SCORE,
            categories=tuple(sorted(categories)),
        )


def _parse_dict(text: str) -> dict[str, ModerationCategory]:
    """Parse the bundled dict format into a `{term: category}` map.

    Format (see `data/sensitive_dict_v0.txt` for the live file):
      * `# <category>` switches the active bucket (lowercase, e.g. `# self_harm`)
      * other `#`-prefixed and blank lines are ignored
      * everything else is a single term added to the active bucket
    """
    terms: dict[str, ModerationCategory] = {}
    active: ModerationCategory | None = None

    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue

        if line.startswith("#"):
            candidate = line.lstrip("#").strip()
            if candidate in _VALID_CATEGORIES:
                active = candidate  # type: ignore[assignment]
            # Anything else under `#` is a free-form comment — ignore.
            continue

        if active is None:
            raise ValueError(f"keyword `{line}` appears before any `# <category>` header")
        if line in terms and terms[line] != active:
            raise ValueError(f"keyword `{line}` is mapped to both `{terms[line]}` and `{active}`")
        terms[line] = active

    if not terms:
        raise ValueError("dict file contained no keywords")
    return terms
