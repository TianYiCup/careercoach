"""Unit tests for the ArcDirector (Character Engine L2).

PR-OPT2: the director is now a pure deterministic edge-resolver with no
LLM dependency. It returns:
  * `opening` on the first turns,
  * `closing` in the tail before the cap,
  * `None` in the middle window — the MoodArbiter then classifies the
    stage in the same call that predicts the mood.

The middle-window classification itself is covered by
`test_mood_arbiter.py::*with_stage*`; here we only assert the edge
guards and the None signal.
"""

from __future__ import annotations

from app.services.sessions.arc_director import ArcDirector, parse_stage


def _resolve(*, turn_index: int, turns_left: int):
    return ArcDirector().resolve(turn_index=turn_index, turns_left=turns_left)


def test_first_turn_is_opening() -> None:
    arc = _resolve(turn_index=1, turns_left=29)

    assert arc is not None
    assert arc.stage == "opening"
    assert "开场" in arc.directive


def test_second_turn_is_opening() -> None:
    arc = _resolve(turn_index=2, turns_left=28)

    assert arc is not None
    assert arc.stage == "opening"


def test_tail_turns_force_closing() -> None:
    """Two turns from the cap → closing. The session must wind down, not
    escalate into a wall."""
    arc = _resolve(turn_index=29, turns_left=1)

    assert arc is not None
    assert arc.stage == "closing"
    assert "收尾" in arc.directive


def test_middle_window_returns_none() -> None:
    """The middle window defers to the arbiter's merged classification."""
    assert _resolve(turn_index=6, turns_left=24) is None
    assert _resolve(turn_index=15, turns_left=15) is None


def test_edge_directive_is_always_populated() -> None:
    """Every resolved edge stage maps to a non-empty directive — the
    arbiter relies on this being present for the mood-only call."""
    for turn_index, turns_left in [(1, 29), (29, 1)]:
        arc = _resolve(turn_index=turn_index, turns_left=turns_left)
        assert arc is not None
        assert arc.directive


def test_parse_stage_matches_middle_keywords() -> None:
    assert parse_stage("turning") == "turning"
    assert parse_stage("「conflict」。") == "conflict"
    assert parse_stage("closing") == "closing"


def test_parse_stage_returns_none_on_garbage() -> None:
    assert parse_stage("我觉得这一轮挺激烈的") is None
