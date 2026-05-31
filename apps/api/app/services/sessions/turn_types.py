"""Turn-flow DTOs + exceptions (extracted from `turn_service.py`).

Split out of the service module so the orchestration file stays under the
file-size budget. Re-exported from `turn_service` so existing import paths
(`from app.services.sessions.turn_service import ValidatedTurn`, and the
package-level `from app.services.sessions import ...`) keep resolving.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.schemas.moderation import RedirectResource
from app.services.sessions.coach_strategy import CoachStrategyRead
from app.services.sessions.turn_repository import CoachHintTrio, TurnRecord


@dataclass(frozen=True)
class CoachResult:
    """What the single coach LLM call yields (PR-L8): the persisted
    three-tone hint trio plus the optional strategy read. `strategy` is
    None when the model didn't emit a parseable on-vocabulary read —
    the caller then omits the strategy card."""

    hints: CoachHintTrio
    strategy: CoachStrategyRead | None


class SessionNotFoundForTurnError(LookupError):
    """Route maps to 404."""


class SessionEndedForTurnError(RuntimeError):
    """Route maps to 409 — can't add turns to an ended session."""


class UserInputBlockedError(RuntimeError):
    """Route maps to 400 — moderation rejected the content."""

    def __init__(self, *, categories: tuple[str, ...]) -> None:
        super().__init__(f"user input blocked by moderation: {categories}")
        self.categories = categories


class ValidatedTurn:
    """Snapshot the route hands to `stream_turn` after `validate_turn_request`.

    Carrying it explicitly forces the route to call validate first and
    surface 4xx errors as real HTTP responses — never via mid-stream
    SSE error frames, which clients handle inconsistently.
    """

    __slots__ = (
        "content",
        "input_verdict",
        "is_minor",
        "moderation_categories",
        "prior_turns",
        "redirect_resource",
        "session_id",
        "trace_id",
        "user_id",
    )

    def __init__(
        self,
        *,
        session_id: str,
        content: str,
        user_id: str,
        trace_id: str,
        prior_turns: list[TurnRecord],
        is_minor: bool = False,
        input_verdict: str = "allow",
        redirect_resource: RedirectResource | None = None,
        moderation_categories: tuple[str, ...] = (),
    ) -> None:
        self.session_id = session_id
        self.content = content
        self.user_id = user_id
        self.trace_id = trace_id
        self.prior_turns = prior_turns
        # A-26: carried out of validate_turn_request so stream_turn can
        # tag the Langfuse trace without re-running moderation. Default
        # values keep older test harnesses that build ValidatedTurn
        # directly from passing.
        self.is_minor = is_minor
        self.input_verdict = input_verdict
        # H-1: only populated when input_verdict == "redirect" — the
        # crisis resource + categories stream_turn emits as the single
        # `moderation` frame (PRD §3.0.5 A). Empty for every other path.
        self.redirect_resource = redirect_resource
        self.moderation_categories = moderation_categories
