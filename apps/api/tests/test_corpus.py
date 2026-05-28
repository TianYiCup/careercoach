"""Tests for the L4 Chinese corpus + trait retrieval."""

from __future__ import annotations

from app.services.scenarios.character_vector import VECTOR_DIMENSIONS, CharacterVector
from app.services.scenarios.corpus import (
    CORPUS,
    CorpusSnippet,
    build_corpus_examples,
    retrieve,
)

# An authority-pressure mood (high aggression/control/power_gap) and a
# peer-deflection mood (low power_gap/aggression) — the two ends used to
# check retrieval actually discriminates.
BOSS_MOOD = CharacterVector(
    aggression=80, empathy=25, control=82, honesty=65, stability=78, power_gap=80
)
PEER_MOOD = CharacterVector(
    aggression=25, empathy=30, control=25, honesty=58, stability=45, power_gap=15
)


def test_corpus_is_non_trivial_and_well_formed() -> None:
    assert len(CORPUS) >= 20
    for snippet in CORPUS:
        assert snippet.text.strip(), "empty corpus line"
        for name in VECTOR_DIMENSIONS:
            value = getattr(snippet.vector, name)
            assert 0 <= value <= 100, f"{snippet.text}: {name}={value} out of range"


def test_retrieve_returns_k_snippets_closest_first() -> None:
    result = retrieve(BOSS_MOOD, k=3)
    assert len(result) == 3
    # Verify ascending distance (closest first) under the same metric.
    from app.services.scenarios.corpus import _distance

    distances = [_distance(s.vector, BOSS_MOOD) for s in result]
    assert distances == sorted(distances)


def test_retrieve_zero_or_negative_k_returns_empty() -> None:
    assert retrieve(BOSS_MOOD, k=0) == []
    assert retrieve(BOSS_MOOD, k=-1) == []


def test_retrieve_caps_at_corpus_size() -> None:
    result = retrieve(BOSS_MOOD, k=10_000)
    assert len(result) == len(CORPUS)


def test_boss_mood_retrieves_authority_register() -> None:
    """High aggression+power_gap should surface an authority-pressure
    line, not a peer-deflection one."""
    top = retrieve(BOSS_MOOD, k=1)[0]
    # The nearest snippet should itself be high on power_gap + aggression.
    assert top.vector.power_gap >= 60
    assert top.vector.aggression >= 55


def test_peer_mood_retrieves_peer_register() -> None:
    top = retrieve(PEER_MOOD, k=1)[0]
    assert top.vector.power_gap <= 30
    assert top.vector.aggression <= 40


def test_contrasting_moods_retrieve_different_snippets() -> None:
    """The smoking gun: a boss mood and a peer mood must not pull the
    same top snippet — otherwise retrieval isn't discriminating."""
    boss_top = retrieve(BOSS_MOOD, k=1)[0]
    peer_top = retrieve(PEER_MOOD, k=1)[0]
    assert boss_top.text != peer_top.text


def test_build_examples_empty_for_no_snippets() -> None:
    assert build_corpus_examples([]) == ""


def test_build_examples_lists_each_snippet_as_a_bullet() -> None:
    snippets = [
        CorpusSnippet("甲", CharacterVector.neutral()),
        CorpusSnippet("乙", CharacterVector.neutral()),
    ]
    block = build_corpus_examples(snippets)
    assert "参考下面这些真实中文语气" in block
    assert "- 甲" in block
    assert "- 乙" in block


def test_build_examples_warns_against_verbatim_copying() -> None:
    """The few-shot framing must tell the model to learn the register,
    not parrot the lines — otherwise the corpus leaks verbatim."""
    block = build_corpus_examples(retrieve(BOSS_MOOD, k=2))
    assert "不要照抄" in block
