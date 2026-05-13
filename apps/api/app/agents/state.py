"""Shared state passed between nodes in the LangGraph state machine.

`SessionState` is a TypedDict so it composes cleanly with LangGraph's
state-merge semantics — each node returns a partial dict and the
graph layers updates on top.

Field meanings (Sprint 0 minimum surface):
  * `scenario_id`     — which scenario from the catalogue we're in
  * `history`         — Message list of the conversation so far,
                        oldest first; the user's latest turn is the
                        last entry with role=user.
  * `user_turn`       — convenience copy of the user's most recent
                        message content; nodes use this instead of
                        re-scanning history.
  * `opponent_reply`  — RolePlay node output (the AI opponent's reply
                        to the user's turn).
  * `coach_hint`      — Coach K node output (next-line guidance).
  * `turn_score`      — Judge node output: verdict + 0-100 rating.

`turn_score` is a small, validated payload (`TurnScore` model) rather
than a free-form dict so the API layer doesn't have to re-validate it.

Naming note: this is `TurnScore` (per-turn, internal graph state),
distinct from `app.schemas.sessions.Score` (per-session, HTTP envelope,
5-dim breakdown). The canonical verdict taxonomy across the codebase
is `shenfeng / guolu / fanche` (design-spec §3.0 评分语义); judge prompt
asks the LLM to emit these literals directly, no translation layer.
"""

from enum import StrEnum
from typing import TypedDict

from pydantic import BaseModel, ConfigDict, Field

from app.llm import Message


class Verdict(StrEnum):
    """Three-tier verdict (design-spec §3.0 评分语义).

    Aligns with `app.schemas.sessions.ScoreResult` so the Judge node
    output and the HTTP `EndSessionResponse.score.result` share the
    same string literals — no translation layer needed at the API
    boundary.
    """

    SHENFENG = "shenfeng"  # 封神 ✨
    GUOLU = "guolu"  # 路过 🌀
    FANCHE = "fanche"  # 翻车 💥


class TurnScore(BaseModel):
    """Judge node output for a single turn."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    verdict: Verdict
    rating: int = Field(ge=0, le=100)


class SessionState(TypedDict, total=False):
    """LangGraph state — every field optional so nodes only set what
    they produce. Use `make_initial_state` to seed a fresh turn."""

    scenario_id: str
    history: list[Message]
    user_turn: str

    opponent_reply: str
    coach_hint: str
    turn_score: TurnScore


def make_initial_state(
    *,
    scenario_id: str,
    history: list[Message],
    user_turn: str,
) -> SessionState:
    """Seed a new turn through the graph.

    Keeping this in one place means the API layer cannot forget a
    required input — the dict literal pattern lets typos slip.
    """
    return SessionState(
        scenario_id=scenario_id,
        history=history,
        user_turn=user_turn,
    )
