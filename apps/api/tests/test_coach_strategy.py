"""Unit tests for coach strategy parsing (Character Engine L8)."""

from __future__ import annotations

from app.services.sessions.coach_strategy import (
    EFFECT_LABELS,
    STRATEGY_LABELS,
    parse_strategy_read,
)

_FULL_COACH_OUTPUT = (
    "SAFE: 我理解你的难处\n"
    "AGGRESSIVE: 这不合理\n"
    "HUMOR: 我去问问菩萨\n"
    "STRATEGY: placate\n"
    "EFFECT: poor\n"
    "UPGRADE: direct"
)


def test_parses_strategy_read_from_full_coach_output() -> None:
    read = parse_strategy_read(_FULL_COACH_OUTPUT)

    assert read is not None
    assert read.strategy == "placate"
    assert read.effect == "poor"
    assert read.upgrade == "direct"


def test_to_dict_round_trips_keys() -> None:
    read = parse_strategy_read(_FULL_COACH_OUTPUT)

    assert read is not None
    assert read.to_dict() == {"strategy": "placate", "effect": "poor", "upgrade": "direct"}


def test_returns_none_when_strategy_line_missing() -> None:
    raw = "SAFE: a\nAGGRESSIVE: b\nHUMOR: c\nEFFECT: poor\nUPGRADE: direct"

    assert parse_strategy_read(raw) is None


def test_returns_none_when_effect_line_missing() -> None:
    raw = "STRATEGY: placate\nUPGRADE: direct"

    assert parse_strategy_read(raw) is None


def test_returns_none_for_off_vocabulary_strategy() -> None:
    """An invented strategy key (model went off-script) → None, so the
    UI drops the card rather than render a key it can't gloss."""
    raw = "STRATEGY: gaslighting\nEFFECT: poor\nUPGRADE: direct"

    assert parse_strategy_read(raw) is None


def test_returns_none_for_off_vocabulary_effect() -> None:
    raw = "STRATEGY: placate\nEFFECT: devastating\nUPGRADE: direct"

    assert parse_strategy_read(raw) is None


def test_returns_none_for_off_vocabulary_upgrade() -> None:
    raw = "STRATEGY: placate\nEFFECT: poor\nUPGRADE: nuke"

    assert parse_strategy_read(raw) is None


def test_case_insensitive_and_fullwidth_colon() -> None:
    raw = "strategy： DIRECT\neffect： Good\nupgrade： direct"

    read = parse_strategy_read(raw)

    assert read is not None
    assert read.strategy == "direct"
    assert read.effect == "good"
    assert read.upgrade == "direct"


def test_upgrade_equal_to_strategy_is_valid_means_hold() -> None:
    raw = "STRATEGY: direct\nEFFECT: good\nUPGRADE: direct"

    read = parse_strategy_read(raw)

    assert read is not None
    assert read.strategy == read.upgrade  # UI renders this as 保持


def test_every_strategy_key_has_a_chinese_gloss() -> None:
    """The wire keys and the display map must stay in sync — a key with
    no gloss would render blank in the UI."""
    for key, label in STRATEGY_LABELS.items():
        assert label, f"strategy key {key} has empty gloss"
    assert "placate" in STRATEGY_LABELS
    assert "direct" in STRATEGY_LABELS


def test_every_effect_key_has_a_chinese_gloss() -> None:
    assert set(EFFECT_LABELS) == {"good", "mixed", "poor"}
    for label in EFFECT_LABELS.values():
        assert label
