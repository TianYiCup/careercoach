"""DictBackend + dict file parser tests (PR ②).

Covers:
  * verdict policy per category (self_harm → redirect, others → block)
  * `self_harm` priority — wins even when other categories also match
  * benign content stays `allow`
  * FP guardrails on a few realistic 大学生 phrases that *look* close
    to red-line but aren't
  * dict file parser: header switching, comment / blank handling,
    duplicate / orphan term errors
  * bundled v0 file loads and has the documented 6 categories

Tests stay deliberately small — the 200-sample regression with
recall / FP-rate gates is PR ④, against the same backend.
"""

from __future__ import annotations

import pytest
from app.schemas.moderation import ModerationCategory
from app.services.moderation.local_dict import (
    DEFAULT_DICT_PATH,
    RED_LINE_SCORE,
    SELF_HARM_RESOURCE,
    SELF_HARM_SCORE,
    DictBackend,
    _parse_dict,
)


@pytest.fixture(scope="module")
def backend() -> DictBackend:
    return DictBackend.from_file()


@pytest.mark.parametrize(
    "content",
    [
        "我最近真的不想活了",
        "想跳楼算了",
        "买一瓶安眠药一了百了",
    ],
)
async def test_self_harm_returns_redirect_with_resource(backend: DictBackend, content: str) -> None:
    decision = await backend.evaluate(content, "user_input")

    assert decision.verdict == "redirect"
    assert decision.redirect_resource == SELF_HARM_RESOURCE
    assert "self_harm" in decision.categories
    assert decision.score == SELF_HARM_SCORE


@pytest.mark.parametrize(
    ("content", "expected"),
    [
        ("放学后我们群殴他一顿", "violence"),
        ("有什么靠谱的网贷可以借", "loan"),
        ("能不能让我摸一下", "harassment"),
        ("打倒共产党！", "political"),
        ("你就是个废物，没人会喜欢你", "other"),
    ],
)
async def test_red_line_categories_return_block(
    backend: DictBackend, content: str, expected: ModerationCategory
) -> None:
    decision = await backend.evaluate(content, "user_input")

    assert decision.verdict == "block"
    assert expected in decision.categories
    assert decision.redirect_resource is None
    assert decision.score == RED_LINE_SCORE


async def test_self_harm_wins_when_mixed_with_other_red_line(
    backend: DictBackend,
) -> None:
    """If both self_harm and another red-line match, redirect wins
    (PRD §3.0.5 A — crisis resource takes precedence over generic block)."""
    decision = await backend.evaluate("你怎么不去死，我也想死了算了", "user_input")

    assert decision.verdict == "redirect"
    assert "self_harm" in decision.categories
    assert "other" in decision.categories  # "你怎么不去死" is sorted under `other`
    assert decision.redirect_resource == SELF_HARM_RESOURCE


@pytest.mark.parametrize(
    "content",
    [
        "今天和朋友一起吃饭，聊了很多",
        "组长说周末加班，我有点犹豫",
        "去图书馆复习，效率挺高",
        "实习面试通过了，下周入职",
        "想跟室友说一下，今天就不一起去",  # near "想死" — must not match (no substring)
    ],
)
async def test_benign_content_stays_allow(backend: DictBackend, content: str) -> None:
    decision = await backend.evaluate(content, "user_input")

    assert decision.verdict == "allow"
    assert decision.categories == ()
    assert decision.score == 0.0
    assert decision.redirect_resource is None


async def test_bundled_dict_has_all_six_categories() -> None:
    """Guard against a future edit that accidentally drops a section."""
    terms = _parse_dict(DEFAULT_DICT_PATH.read_text(encoding="utf-8"))
    categories = set(terms.values())

    assert categories == {
        "self_harm",
        "violence",
        "loan",
        "harassment",
        "political",
        "other",
    }


async def test_bundled_dict_has_at_least_two_hundred_terms() -> None:
    """Task split doc requires 200-word v0 coverage."""
    terms = _parse_dict(DEFAULT_DICT_PATH.read_text(encoding="utf-8"))
    assert len(terms) >= 200, f"dict shrunk to {len(terms)} terms"


def test_parse_dict_switches_active_category() -> None:
    text = """
    # self_harm
    自杀
    跳楼

    # violence
    群殴
    """
    terms = _parse_dict(text)

    assert terms == {
        "自杀": "self_harm",
        "跳楼": "self_harm",
        "群殴": "violence",
    }


def test_parse_dict_ignores_comments_and_blank_lines() -> None:
    text = """
    # this is a header comment that is not a category

    # self_harm
    # inline comment under section
    自杀

    """
    terms = _parse_dict(text)

    assert terms == {"自杀": "self_harm"}


def test_parse_dict_rejects_terms_before_first_category() -> None:
    text = """
    # this is just a doc comment, not a category
    自杀
    """
    with pytest.raises(ValueError, match="before any"):
        _parse_dict(text)


def test_parse_dict_rejects_term_in_two_categories() -> None:
    text = """
    # self_harm
    词A

    # violence
    词A
    """
    with pytest.raises(ValueError, match="mapped to both"):
        _parse_dict(text)


def test_parse_dict_rejects_empty_text() -> None:
    with pytest.raises(ValueError, match="no keywords"):
        _parse_dict("# only_a_doc_comment\n# self_harm\n")


def test_dict_backend_rejects_empty_terms() -> None:
    with pytest.raises(ValueError, match="at least one term"):
        DictBackend({})


async def test_from_text_round_trips() -> None:
    backend = DictBackend.from_text("# self_harm\n自杀\n# violence\n群殴\n")

    self_harm = await backend.evaluate("我想自杀", "user_input")
    assert self_harm.verdict == "redirect"

    violence = await backend.evaluate("我们去群殴他", "user_input")
    assert violence.verdict == "block"
    assert violence.categories == ("violence",)

    benign = await backend.evaluate("今天天气真好", "user_input")
    assert benign.verdict == "allow"
