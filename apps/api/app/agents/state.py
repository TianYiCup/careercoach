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
  * `score`           — Judge node output: verdict + 0-100 rating.

`score` is a small, validated payload (`Score` model) rather than a
free-form dict so the API layer doesn't have to re-validate it.
"""

from enum import StrEnum
from typing import TypedDict

from pydantic import BaseModel, ConfigDict, Field

from app.llm import Message


class Verdict(StrEnum):
    """Three-tier verdict (design-spec §3.0 评分语义)."""

    GODLIKE = "godlike"  # 封神 ✨
    PASS = "pass"  # 路过 🌀
    FAIL = "fail"  # 翻车 💥


class Score(BaseModel):
    """Judge node output."""

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
    score: Score


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
