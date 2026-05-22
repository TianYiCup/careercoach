"""Catalog invariant tests for the 4 base opponent personas (US-A2).

These guard the *content* contract PRD §10.2 / §6 mandate — fixed set
of 4, each with a ≥ 200-char four-element `system_prompt` — separately
from the HTTP-shape tests in `test_personas_route.py`.
"""

from __future__ import annotations

from app.services.personas import get_persona, list_personas

_EXPECTED_IDS = {"p_mild", "p_hard", "p_pua", "p_sarcastic"}

# The four elements PRD US-A2 requires every persona system prompt to spell out.
_REQUIRED_PROMPT_ELEMENTS = ("说话风格", "价值观", "典型口头禅", "不会做什么")


def test_catalog_has_exactly_four_fixed_personas() -> None:
    personas = list_personas()
    assert len(personas) == 4
    assert {p.id for p in personas} == _EXPECTED_IDS


def test_persona_ids_are_unique() -> None:
    ids = [p.id for p in list_personas()]
    assert len(ids) == len(set(ids))


def test_personas_are_ordered_easy_to_hard() -> None:
    """The picker renders them 从易到难 without re-sorting (PRD US-A2)."""
    difficulties = [p.difficulty for p in list_personas()]
    assert difficulties == sorted(difficulties)


def test_every_difficulty_is_in_range() -> None:
    assert all(1 <= p.difficulty <= 5 for p in list_personas())


def test_every_system_prompt_is_at_least_200_chars() -> None:
    """PRD US-A2: each persona system prompt must be ≥ 200 字."""
    for persona in list_personas():
        assert len(persona.system_prompt) >= 200, persona.id


def test_every_system_prompt_spells_out_the_four_elements() -> None:
    """PRD US-A2: 说话风格 / 价值观 / 典型口头禅 / 不会做什么."""
    for persona in list_personas():
        for element in _REQUIRED_PROMPT_ELEMENTS:
            assert element in persona.system_prompt, f"{persona.id} missing {element}"


def test_card_facing_fields_are_non_empty() -> None:
    for persona in list_personas():
        assert persona.name
        assert persona.style
        assert persona.avatar
        assert persona.background
        assert persona.age >= 1


def test_get_persona_returns_record_for_known_id() -> None:
    persona = get_persona("p_hard")
    assert persona is not None
    assert persona.id == "p_hard"
    assert persona.style == "强硬型"


def test_get_persona_returns_none_for_unknown_id() -> None:
    assert get_persona("p_does_not_exist") is None
