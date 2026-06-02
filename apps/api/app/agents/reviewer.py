"""Reviewer agent — turns a raw chat dump into three-color marks + summary.

PRD §3.3 US-C2 (复盘师): the user uploads a chat fragment (text in
v0; OCR / ASR transcripts later), and we mark every turn as
`win` / `neutral` / `lose`, attach `reason` + `better` to the user's
`lose` turns, and emit one summary score with top failures + suggested
improvements.

Why this lives next to the existing nodes
-----------------------------------------
The reviewer is a one-shot LLM call (no multi-node graph) so it doesn't
need its own LangGraph wrapper — but the prompt + parser shape mirrors
`summarizer.py` exactly so the code is shaped like a node author would
expect when they extend this for OCR (Sprint 1) or for a multi-pass
critic (Sprint 2).

Why one big LLM call instead of per-turn
----------------------------------------
The summary score reflects the WHOLE conversation, so a per-turn loop
would either re-show context every call (cost ×N) or run a second
pass for the summary (cost ×2). A single call with a strict numbered
contract is cheaper, faster, and lets the model see cross-turn pattern
(e.g. one bad close after three good lines).

Output contract degradation
---------------------------
The model sometimes skips a TURN line. Rather than fail the upload,
missing turns degrade to `neutral` with no reason / better, and a
WARNING fires per skipped index so it's visible in trace. Hard
fallbacks (return `None` → caller calls `mark_failed`) only fire when
the SUMMARY block is unparseable; without a summary score the route
layer has nothing to show the user.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import cast

import structlog

from app.agents._stream import stream_to_text
from app.llm import LLMProvider, Message, TokenUsage
from app.services.review import ReviewTurnRecord, ReviewVerdict, Speaker

logger = structlog.get_logger(__name__)

# Backstop on prompt size — the route layer caps `text` at 5000 chars
# upstream (see `MAX_REVIEW_TEXT_LENGTH` in `app.schemas.review`), so a
# 50-turn ceiling here is just a defence-in-depth.
MAX_REVIEW_TURNS = 50

# Per PRD §3.3 US-C2 the summary surfaces "最多 3 条" of each list.
MAX_SUMMARY_ITEMS = 3

# Match the A-9 schema column widths so we never insert a value that
# would be silently truncated by Postgres.
_REASON_MAX = 500  # review_turns.reason: String(500)
_BETTER_MAX = 500  # review_turns.better: String(500)
_TOP_FAILURE_MAX = 64  # review_uploads.summary_top_failures: ARRAY(String(64))
_IMPROVEMENT_MAX = 200  # review_uploads.summary_improvements: ARRAY(String(200))

# Speaker prefixes — left side of "<prefix>: <content>". Both ASCII
# colon and the CJK fullwidth colon are accepted; lowercased before
# lookup so case mismatches don't misclassify English prefixes.
_OPPONENT_PREFIXES: frozenset[str] = frozenset(
    {
        "对方",
        "对手",
        "他",
        "她",
        "他们",
        "boss",
        "老板",
        "hr",
        "面试官",
        "opponent",
        "them",
    }
)
_USER_PREFIXES: frozenset[str] = frozenset(
    {
        "我",
        "自己",
        "user",
        "me",
    }
)

# `[^\s:：]+` — one or more non-whitespace, non-colon chars; the
# explicit fullwidth colon codepoint avoids depending on Unicode classes
# while still accepting CN-input chat dumps.
_PREFIX_RE = re.compile(r"^\s*([^\s:：]+)\s*[:：]\s*(.+)$")


@dataclass(frozen=True)
class ParsedTurn:
    """One line of input chat after speaker detection.

    Plain dataclass rather than the persistence `ReviewTurnRecord` —
    until the LLM has spoken we don't have a verdict, so this is the
    upstream shape, not the eventual record.
    """

    speaker: Speaker
    content: str


@dataclass(frozen=True)
class ReviewerResult:
    """Reviewer LLM output normalised into the persistence contract.

    `turns` is already in the shape `update_result` accepts. The route
    layer (A-11) will pass these tuples straight into the repository
    without further transformation.
    """

    turns: tuple[ReviewTurnRecord, ...]
    summary_score: float
    summary_top_failures: tuple[str, ...]
    summary_improvements: tuple[str, ...]


def parse_review_text(text: str) -> tuple[ParsedTurn, ...]:
    """Split a raw chat dump into ParsedTurn entries.

    * Each non-empty line becomes one turn.
    * `对方: …` / `我: …` / `me: …` / `opponent: …` etc. tag the speaker.
    * Lines without a recognised prefix default to `user` — this is the
      user reflecting on their own conversation, so an unattributed line
      is most likely something they said. A DEBUG log fires so flaky
      input formats are visible.
    * Capped at `MAX_REVIEW_TURNS`; extra lines are silently dropped.
    """
    turns: list[ParsedTurn] = []
    for raw_line in text.splitlines():
        if len(turns) >= MAX_REVIEW_TURNS:
            break
        line = raw_line.strip()
        if not line:
            continue
        match = _PREFIX_RE.match(line)
        if match is not None:
            prefix = match.group(1).strip().lower()
            content = match.group(2).strip()
            if prefix in _OPPONENT_PREFIXES:
                turns.append(ParsedTurn(speaker="opponent", content=content))
                continue
            if prefix in _USER_PREFIXES:
                turns.append(ParsedTurn(speaker="user", content=content))
                continue
            logger.debug("review_unknown_prefix", prefix=prefix)
        turns.append(ParsedTurn(speaker="user", content=line))
    return tuple(turns)


REVIEWER_SYSTEM_PROMPT = (
    "你是复盘师。看完用户提供的对话片段，对【每一句】打三色标记，并给整段一个总评。\n"
    "三色含义：win=封神，neutral=路过，lose=翻车。\n"
    "只对【用户】(USER) 的 lose 句子写 REASON 和 BETTER；对手(OPPONENT) 的句子只标 verdict，"
    "不写 reason / better；用户的 win / neutral 句子也不写 reason / better。\n"
    "REASON 解释失分原因，BETTER 给一句 ≤ 200 字、用户可以直接照说的更佳话术。\n"
    "严格按以下格式输出，不要解释、不要任何额外文字：\n"
    "TURN 0 | VERDICT: <win|neutral|lose>[ | REASON: ...][ | BETTER: ...]\n"
    "TURN 1 | VERDICT: ...\n"
    "...\n"
    "---\n"
    "SCORE: <0-10 一位小数，整段分>\n"
    "TOP_FAILURES: <最多 3 条简短失分点；用 ; 分隔>\n"
    "IMPROVEMENTS: <最多 3 条具体可执行建议；用 ; 分隔>"
)


_SUMMARY_SEPARATOR_RE = re.compile(r"^\s*-{3,}\s*$", re.MULTILINE)
# Fallback anchor for when the model omits the `---` line and jumps
# straight to the summary: the first line that starts with `SCORE:`.
# `[^\S\n]*` allows leading spaces/tabs but not a newline, so `^` stays
# pinned to a real line start under re.MULTILINE.
_SUMMARY_ANCHOR_RE = re.compile(r"^[^\S\n]*SCORE\s*:", re.IGNORECASE | re.MULTILINE)
_TURN_LINE_RE = re.compile(
    r"^TURN\s+(\d+)\s*\|\s*VERDICT\s*:\s*(\w+)(.*)$",
    re.IGNORECASE | re.MULTILINE,
)
_REASON_FRAGMENT_RE = re.compile(r"\|\s*REASON\s*:\s*([^|]+)", re.IGNORECASE)
_BETTER_FRAGMENT_RE = re.compile(r"\|\s*BETTER\s*:\s*([^|]+)", re.IGNORECASE)
_SCORE_RE = re.compile(r"SCORE\s*:\s*([0-9]+(?:\.[0-9]+)?)", re.IGNORECASE)
_TOP_FAILURES_RE = re.compile(r"TOP_FAILURES\s*:\s*(.+)", re.IGNORECASE)
_IMPROVEMENTS_RE = re.compile(r"IMPROVEMENTS\s*:\s*(.+)", re.IGNORECASE)

_VALID_VERDICTS: frozenset[str] = frozenset({"win", "neutral", "lose"})


def _split_summary_items(raw: str, *, item_max_len: int) -> tuple[str, ...]:
    """Split on `;` or fullwidth `；`, strip, drop blanks, cap to MAX."""
    items: list[str] = []
    for chunk in re.split(r"[;；]", raw):
        cleaned = chunk.strip()
        if not cleaned:
            continue
        items.append(cleaned[:item_max_len])
        if len(items) >= MAX_SUMMARY_ITEMS:
            break
    return tuple(items)


def _split_turn_and_summary(text: str) -> tuple[str, str] | None:
    """Split the reviewer output into `(turn_block, summary_block)`.

    The prompt asks for a `---` line between the per-turn marks and the
    summary, but the model frequently omits it and goes straight to
    `SCORE:`. Be tolerant:
      1. prefer the explicit `---` separator when present;
      2. otherwise anchor on the first `SCORE:`-led line — everything
         before it is the turn block, everything from it onward is the
         summary;
      3. give up only when there is no anchor at all (no `---` AND no
         `SCORE:` line), i.e. the output is genuinely unusable.
    """
    parts = _SUMMARY_SEPARATOR_RE.split(text, maxsplit=1)
    if len(parts) == 2:
        return parts[0], parts[1]
    anchor = _SUMMARY_ANCHOR_RE.search(text)
    if anchor is None:
        return None
    return text[: anchor.start()], text[anchor.start() :]


def parse_reviewer_output(
    text: str,
    parsed_turns: tuple[ParsedTurn, ...],
) -> ReviewerResult | None:
    """Best-effort parse of the reviewer LLM contract.

    Returns None only when there is no usable SCORE: the summary anchor
    is missing (no `---` and no `SCORE:` line), the SCORE field is
    absent, non-numeric, or outside [0, 10]. Softer drift degrades
    instead of failing: a missing `---` separator falls back to the
    `SCORE:` anchor, omitted TOP_FAILURES / IMPROVEMENTS become empty
    lists, and per-turn lines the LLM skipped degrade to `neutral` —
    we still produce a complete record with the score + turns.

    Public so tests can drive parsing without spinning up an LLM.
    """
    split = _split_turn_and_summary(text)
    if split is None:
        logger.warning("reviewer_missing_summary", raw=text[:200])
        return None
    turn_block, summary_block = split

    score_match = _SCORE_RE.search(summary_block)
    if score_match is None:
        logger.warning("reviewer_missing_score", raw=text[:200])
        return None
    try:
        score = float(score_match.group(1))
    except ValueError:
        logger.warning("reviewer_score_not_float", raw=text[:200])
        return None
    if not 0.0 <= score <= 10.0:
        logger.warning("reviewer_score_out_of_range", value=score)
        return None

    # TOP_FAILURES / IMPROVEMENTS are nice-to-have: if the model omits
    # one, degrade to an empty list rather than failing the whole upload
    # (the score + turns are the load-bearing result). The result page
    # already hides empty summary sections.
    failures_match = _TOP_FAILURES_RE.search(summary_block)
    improvements_match = _IMPROVEMENTS_RE.search(summary_block)
    if failures_match is None or improvements_match is None:
        logger.info(
            "reviewer_partial_summary",
            has_failures=failures_match is not None,
            has_improvements=improvements_match is not None,
        )

    top_failures = (
        _split_summary_items(failures_match.group(1), item_max_len=_TOP_FAILURE_MAX)
        if failures_match is not None
        else ()
    )
    improvements = (
        _split_summary_items(improvements_match.group(1), item_max_len=_IMPROVEMENT_MAX)
        if improvements_match is not None
        else ()
    )

    by_idx: dict[int, tuple[str, str | None, str | None]] = {}
    for match in _TURN_LINE_RE.finditer(turn_block):
        idx = int(match.group(1))
        verdict_raw = match.group(2).lower()
        if verdict_raw not in _VALID_VERDICTS:
            logger.warning("reviewer_unknown_verdict", verdict=verdict_raw, turn=idx)
            continue
        tail = match.group(3) or ""
        reason_match = _REASON_FRAGMENT_RE.search(tail)
        better_match = _BETTER_FRAGMENT_RE.search(tail)
        reason = reason_match.group(1).strip()[:_REASON_MAX] if reason_match else None
        better = better_match.group(1).strip()[:_BETTER_MAX] if better_match else None
        by_idx[idx] = (verdict_raw, reason, better)

    turn_records: list[ReviewTurnRecord] = []
    for idx, parsed in enumerate(parsed_turns):
        verdict, reason, better = by_idx.get(idx, ("neutral", None, None))
        if idx not in by_idx:
            logger.warning("reviewer_missing_turn", turn=idx)
        # PRD §3.3 US-C2: reason / better are user-side coaching only,
        # and only for `lose` turns. Drop anything the LLM emitted in
        # other slots so the persisted record stays clean.
        if verdict != "lose" or parsed.speaker != "user":
            reason = None
            better = None
        turn_records.append(
            ReviewTurnRecord(
                turn_idx=idx,
                speaker=parsed.speaker,
                content=parsed.content,
                verdict=cast(ReviewVerdict, verdict),
                reason=reason,
                better=better,
            )
        )

    return ReviewerResult(
        turns=tuple(turn_records),
        summary_score=score,
        summary_top_failures=top_failures,
        summary_improvements=improvements,
    )


async def analyze_review(
    provider: LLMProvider,
    *,
    text: str,
    usage_sink: list[TokenUsage] | None = None,
) -> ReviewerResult | None:
    """Raw text → parsed turns → LLM call → ReviewerResult.

    Returns None when:
      * no parseable turns in input (caller should mark_failed) or
      * LLM output cannot be parsed past the summary block.

    The route layer (A-11) is responsible for translating None into
    `repo.mark_failed(...)` and a non-None result into
    `repo.update_result(...)`.

    `usage_sink` forwards to the provider (A-27) so the caller can
    record the underlying LLM's token counts on the Langfuse trace.
    Stays optional — callers that don't care still get a one-call
    surface with no extra plumbing.
    """
    parsed_turns = parse_review_text(text)
    if not parsed_turns:
        logger.warning("reviewer_no_turns_in_input", text_len=len(text))
        return None

    numbered = "\n".join(
        f"TURN {idx} | {turn.speaker.upper()}: {turn.content}"
        for idx, turn in enumerate(parsed_turns)
    )
    user_prompt = f"对话内容：\n{numbered}\n\n请按系统消息中的格式输出。"
    messages: list[Message] = [
        Message.system(REVIEWER_SYSTEM_PROMPT),
        Message.user(user_prompt),
    ]

    raw = await stream_to_text(provider.stream_chat(messages, usage_sink=usage_sink))
    return parse_reviewer_output(raw, parsed_turns)


__all__ = [
    "MAX_REVIEW_TURNS",
    "MAX_SUMMARY_ITEMS",
    "REVIEWER_SYSTEM_PROMPT",
    "ParsedTurn",
    "ReviewerResult",
    "analyze_review",
    "parse_review_text",
    "parse_reviewer_output",
]
