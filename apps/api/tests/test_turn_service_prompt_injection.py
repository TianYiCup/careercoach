"""End-to-end check that L1.3 wires the descriptor into the actual
prompts the LLM sees.

The two unit-test files cover the pieces in isolation:
  * `test_character_vector.py` — descriptor functions produce expected
    bullets;
  * existing `test_sessions_turn_service.py` — TurnService orchestration.

This file pins the *integration* — that the prompt builder consumes
the descriptor parameter and that an empty descriptor collapses back
to the pre-L1.3 prompt shape, so an unmigrated custom scenario still
sees the same string an L1.1 build would have rendered.
"""

from __future__ import annotations

from app.services.scenarios.character_vector import (
    CharacterVector,
    describe_for_coach,
    describe_for_roleplay,
)
from app.services.sessions.turn_service import _build_coach_prompt, _build_roleplay_prompt


def test_roleplay_prompt_contains_descriptor_block_for_high_intensity_persona() -> None:
    vector = CharacterVector(
        aggression=85,
        empathy=20,
        control=80,
        honesty=25,
        stability=80,
        power_gap=85,
    )

    prompt = _build_roleplay_prompt(
        scenario_title="周末加班谈判",
        background="老板群里@你周末加班",
        persona_title="强硬型 HR",
        user_goal="拒绝加班但不撕破脸",
        character_descriptor=describe_for_roleplay(vector),
    )

    assert "你的性格底色：" in prompt
    assert "说话带刺" in prompt
    assert "是上位者" in prompt
    # The descriptor block sits between user_goal and the adversarial
    # framing line — pin it so a future reorder doesn't put the persona
    # paragraph below the "你站在对立面" instruction (which would
    # make the LLM weight the descriptor less).
    user_goal_idx = prompt.index("用户的目标是：")
    descriptor_idx = prompt.index("你的性格底色：")
    framing_idx = prompt.index("你站在与用户对立的一方")
    assert user_goal_idx < descriptor_idx < framing_idx


def test_roleplay_prompt_collapses_to_baseline_for_neutral_vector() -> None:
    """An L1.1 build (no descriptor) and an L1.3 build with a neutral
    vector should render identical prompts — the empty descriptor path
    must not introduce stray blank lines or trailing whitespace."""
    neutral_prompt = _build_roleplay_prompt(
        scenario_title="X",
        background="Y",
        persona_title="Z",
        user_goal="W",
        character_descriptor=describe_for_roleplay(CharacterVector.neutral()),
    )
    legacy_prompt = _build_roleplay_prompt(
        scenario_title="X",
        background="Y",
        persona_title="Z",
        user_goal="W",
    )

    assert neutral_prompt == legacy_prompt
    assert "你的性格底色" not in neutral_prompt


def test_coach_prompt_contains_opponent_profile_for_powerful_opponent() -> None:
    vector = CharacterVector(
        aggression=50,
        empathy=50,
        control=50,
        honesty=15,
        stability=80,
        power_gap=85,
    )

    prompt = _build_coach_prompt(
        scenario_title="周末加班谈判",
        user_goal="拒绝加班但不撕破脸",
        opponent_profile=describe_for_coach(vector),
    )

    assert "对手画像：" in prompt
    assert "上位者" in prompt
    assert "情绪很稳" in prompt
    assert "兜圈子" in prompt
    # K's user-side framing must still come after the opponent profile,
    # not before — otherwise the model reads tactical advice before
    # being told which side of the conversation it's on.
    profile_idx = prompt.index("对手画像：")
    framing_idx = prompt.index("你站在【用户这一边】")
    assert profile_idx < framing_idx


def test_coach_prompt_collapses_to_baseline_for_neutral_opponent() -> None:
    neutral_prompt = _build_coach_prompt(
        scenario_title="X",
        user_goal="W",
        opponent_profile=describe_for_coach(CharacterVector.neutral()),
    )
    legacy_prompt = _build_coach_prompt(scenario_title="X", user_goal="W")

    assert neutral_prompt == legacy_prompt
    assert "对手画像" not in neutral_prompt
