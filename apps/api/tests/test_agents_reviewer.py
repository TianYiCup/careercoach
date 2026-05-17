"""Tests for `app.agents.reviewer`.

Three layers:
  * `parse_review_text` — pure input parser; no LLM
  * `parse_reviewer_output` — pure output parser; no LLM
  * `analyze_review` — end-to-end with a fake provider that returns a
    canned string

The split lets parser bugs surface without spinning up async machinery,
and lets us assert on the full pipeline once those layers are pinned.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

from app.agents.reviewer import (
    MAX_REVIEW_TURNS,
    MAX_SUMMARY_ITEMS,
    ParsedTurn,
    analyze_review,
    parse_review_text,
    parse_reviewer_output,
)
from app.llm import Message, TokenUsage
from app.services.review import ReviewTurnRecord

# --------------------------------------------------------------------- #
# parse_review_text                                                      #
# --------------------------------------------------------------------- #


def test_parse_review_text_chinese_prefixes_recognised() -> None:
    text = "对方: hello\n我: hi back\n他: again\n自己: ok"
    parsed = parse_review_text(text)

    assert parsed == (
        ParsedTurn(speaker="opponent", content="hello"),
        ParsedTurn(speaker="user", content="hi back"),
        ParsedTurn(speaker="opponent", content="again"),
        ParsedTurn(speaker="user", content="ok"),
    )


def test_parse_review_text_english_prefixes_case_insensitive() -> None:
    text = "OPPONENT: hi\nMe: hello\nUser: still me\nThem: third party"
    parsed = parse_review_text(text)

    assert parsed[0] == ParsedTurn(speaker="opponent", content="hi")
    assert parsed[1] == ParsedTurn(speaker="user", content="hello")
    assert parsed[2] == ParsedTurn(speaker="user", content="still me")
    assert parsed[3] == ParsedTurn(speaker="opponent", content="third party")


def test_parse_review_text_role_aliases_for_chat_dumps() -> None:
    text = "HR: please describe yourself\nme: a few highlights\nBoss: not enough"
    parsed = parse_review_text(text)

    assert parsed[0].speaker == "opponent"
    assert parsed[1].speaker == "user"
    assert parsed[2].speaker == "opponent"


def test_parse_review_text_unprefixed_lines_default_to_user() -> None:
    text = "just a thought\nanother one"
    parsed = parse_review_text(text)

    assert parsed == (
        ParsedTurn(speaker="user", content="just a thought"),
        ParsedTurn(speaker="user", content="another one"),
    )


def test_parse_review_text_unknown_prefix_keeps_full_line_as_user() -> None:
    text = "Alice: random name not in our prefix list"
    parsed = parse_review_text(text)

    assert len(parsed) == 1
    assert parsed[0].speaker == "user"
    assert "Alice" in parsed[0].content


def test_parse_review_text_blank_lines_skipped() -> None:
    text = "\n\nuser: hi\n\n\nopponent: hello\n   \n"
    parsed = parse_review_text(text)

    assert len(parsed) == 2


def test_parse_review_text_empty_input_returns_empty_tuple() -> None:
    assert parse_review_text("") == ()
    assert parse_review_text("   \n\n  \n") == ()


def test_parse_review_text_caps_at_max_turns() -> None:
    text = "\n".join(f"user: line {i}" for i in range(MAX_REVIEW_TURNS + 20))
    parsed = parse_review_text(text)

    assert len(parsed) == MAX_REVIEW_TURNS
    # The cap drops trailing lines, not leading ones.
    assert parsed[0].content == "line 0"
    assert parsed[-1].content == f"line {MAX_REVIEW_TURNS - 1}"


# --------------------------------------------------------------------- #
# parse_reviewer_output                                                  #
# --------------------------------------------------------------------- #


def _two_turns() -> tuple[ParsedTurn, ...]:
    return (
        ParsedTurn(speaker="opponent", content="free this weekend?"),
        ParsedTurn(speaker="user", content="busy."),
    )


def test_parse_reviewer_output_happy_path() -> None:
    raw = (
        "TURN 0 | VERDICT: neutral\n"
        "TURN 1 | VERDICT: lose | REASON: too cold | BETTER: have plans, raincheck Tuesday?\n"
        "---\n"
        "SCORE: 6.4\n"
        "TOP_FAILURES: too curt; no warmth\n"
        "IMPROVEMENTS: offer alt slot; show empathy"
    )

    result = parse_reviewer_output(raw, _two_turns())

    assert result is not None
    assert result.summary_score == 6.4
    assert result.summary_top_failures == ("too curt", "no warmth")
    assert result.summary_improvements == ("offer alt slot", "show empathy")
    assert result.turns == (
        ReviewTurnRecord(
            turn_idx=0, speaker="opponent", content="free this weekend?", verdict="neutral"
        ),
        ReviewTurnRecord(
            turn_idx=1,
            speaker="user",
            content="busy.",
            verdict="lose",
            reason="too cold",
            better="have plans, raincheck Tuesday?",
        ),
    )


def test_parse_reviewer_output_missing_turn_degrades_to_neutral() -> None:
    raw = (
        # TURN 1 deliberately omitted
        "TURN 0 | VERDICT: win\n---\nSCORE: 5.0\nTOP_FAILURES: x\nIMPROVEMENTS: y"
    )

    result = parse_reviewer_output(raw, _two_turns())

    assert result is not None
    assert result.turns[1].verdict == "neutral"
    assert result.turns[1].reason is None
    assert result.turns[1].better is None


def test_parse_reviewer_output_drops_reason_better_for_non_lose_user_turns() -> None:
    """Per PRD: reason / better only on `lose` user turns."""
    parsed = (
        ParsedTurn(speaker="user", content="line a"),
        ParsedTurn(speaker="user", content="line b"),
    )
    raw = (
        # win + neutral both have stray REASON/BETTER from the LLM —
        # parser must drop them to keep the persisted record clean.
        "TURN 0 | VERDICT: win | REASON: nice | BETTER: even nicer\n"
        "TURN 1 | VERDICT: neutral | REASON: meh\n"
        "---\n"
        "SCORE: 7.0\n"
        "TOP_FAILURES: -\n"
        "IMPROVEMENTS: -"
    )

    result = parse_reviewer_output(raw, parsed)

    assert result is not None
    assert result.turns[0].reason is None
    assert result.turns[0].better is None
    assert result.turns[1].reason is None
    assert result.turns[1].better is None


def test_parse_reviewer_output_drops_reason_better_for_opponent_turns() -> None:
    """Opponent turns never carry coaching even if the LLM provided it."""
    parsed = (
        ParsedTurn(speaker="opponent", content="op line"),
        ParsedTurn(speaker="user", content="user line"),
    )
    raw = (
        "TURN 0 | VERDICT: lose | REASON: nope | BETTER: ditto\n"
        "TURN 1 | VERDICT: lose | REASON: real | BETTER: actual fix\n"
        "---\n"
        "SCORE: 3.0\n"
        "TOP_FAILURES: a\n"
        "IMPROVEMENTS: b"
    )

    result = parse_reviewer_output(raw, parsed)

    assert result is not None
    assert result.turns[0].reason is None
    assert result.turns[0].better is None
    assert result.turns[1].reason == "real"
    assert result.turns[1].better == "actual fix"


def test_parse_reviewer_output_unknown_verdict_falls_back_to_neutral() -> None:
    raw = (
        "TURN 0 | VERDICT: glorious\n"  # not in {win, neutral, lose}
        "TURN 1 | VERDICT: win\n"
        "---\n"
        "SCORE: 8.0\n"
        "TOP_FAILURES: a\n"
        "IMPROVEMENTS: b"
    )

    result = parse_reviewer_output(raw, _two_turns())

    assert result is not None
    assert result.turns[0].verdict == "neutral"
    assert result.turns[1].verdict == "win"


def test_parse_reviewer_output_score_out_of_range_returns_none() -> None:
    raw = (
        "TURN 0 | VERDICT: win\n"
        "TURN 1 | VERDICT: win\n"
        "---\n"
        "SCORE: 99\n"
        "TOP_FAILURES: x\n"
        "IMPROVEMENTS: y"
    )

    assert parse_reviewer_output(raw, _two_turns()) is None


def test_parse_reviewer_output_missing_summary_separator_returns_none() -> None:
    raw = (
        "TURN 0 | VERDICT: win\nTURN 1 | VERDICT: win\n"
        # no `---`, no summary block
    )

    assert parse_reviewer_output(raw, _two_turns()) is None


def test_parse_reviewer_output_missing_score_returns_none() -> None:
    raw = "TURN 0 | VERDICT: win\nTURN 1 | VERDICT: win\n---\nTOP_FAILURES: x\nIMPROVEMENTS: y"

    assert parse_reviewer_output(raw, _two_turns()) is None


def test_parse_reviewer_output_caps_summary_items_at_max() -> None:
    raw = (
        "TURN 0 | VERDICT: win\n"
        "TURN 1 | VERDICT: win\n"
        "---\n"
        "SCORE: 5.0\n"
        "TOP_FAILURES: a; b; c; d; e\n"
        "IMPROVEMENTS: 1; 2; 3; 4"
    )

    result = parse_reviewer_output(raw, _two_turns())

    assert result is not None
    assert len(result.summary_top_failures) == MAX_SUMMARY_ITEMS
    assert result.summary_top_failures == ("a", "b", "c")
    assert len(result.summary_improvements) == MAX_SUMMARY_ITEMS


def test_parse_reviewer_output_garbage_returns_none() -> None:
    assert parse_reviewer_output("the model wrote a sonnet", _two_turns()) is None
    assert parse_reviewer_output("", _two_turns()) is None


# --------------------------------------------------------------------- #
# analyze_review (end-to-end with fake provider)                         #
# --------------------------------------------------------------------- #


class _FakeProvider:
    """Stub LLMProvider that returns a fixed string from `stream_chat`.

    Captures the messages it received so tests can assert that the
    system prompt + user prompt actually flowed through.
    """

    name = "fake"

    def __init__(self, output: str) -> None:
        self._output = output
        self.received: list[Message] | None = None

    def stream_chat(
        self,
        messages: list[Message],
        *,
        temperature: float = 0.7,
        timeout: float = 8.0,
        usage_sink: list[TokenUsage] | None = None,
    ) -> AsyncIterator[str]:
        _ = usage_sink
        self.received = list(messages)
        return self._chunks()

    async def _chunks(self) -> AsyncIterator[str]:
        # Yield in two halves to also exercise the accumulator's
        # multi-chunk path.
        yield self._output[: len(self._output) // 2]
        yield self._output[len(self._output) // 2 :]


async def test_analyze_review_end_to_end_happy_path() -> None:
    provider = _FakeProvider(
        "TURN 0 | VERDICT: neutral\n"
        "TURN 1 | VERDICT: lose | REASON: too cold | BETTER: warmer alternative\n"
        "---\n"
        "SCORE: 6.4\n"
        "TOP_FAILURES: too curt\n"
        "IMPROVEMENTS: offer alt"
    )
    text = "opponent: free this weekend?\nme: busy."

    result = await analyze_review(provider, text=text)

    assert result is not None
    assert result.summary_score == 6.4
    assert result.turns[0].speaker == "opponent"
    assert result.turns[0].verdict == "neutral"
    assert result.turns[1].speaker == "user"
    assert result.turns[1].verdict == "lose"
    assert result.turns[1].better == "warmer alternative"
    # The fake provider saw the system prompt + a user prompt.
    assert provider.received is not None
    assert len(provider.received) == 2
    assert provider.received[0].role.value == "system"
    assert provider.received[1].role.value == "user"


async def test_analyze_review_returns_none_when_input_has_no_turns() -> None:
    provider = _FakeProvider("doesn't matter; should not be called meaningfully")

    assert await analyze_review(provider, text="   \n\n   ") is None


async def test_analyze_review_returns_none_when_llm_output_unparseable() -> None:
    provider = _FakeProvider("the model wrote a sonnet instead of the contract")

    result = await analyze_review(provider, text="me: hi\nopponent: hi back")

    assert result is None
