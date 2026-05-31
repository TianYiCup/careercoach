"""Sandbox turn prompts + response parsing (extracted from `turn_service.py`).

Pure functions and constants — no service state. Holds the roleplay /
coach system-prompt builders, the judge prompt, the coach three-tone
parser, and the chat-history reconstruction the roleplay LLM sees.

Split out so `turn_service.py` stays under the file-size budget; the
prompt builders are imported back into the service (and stay importable
from it, so `tests/test_turn_service_prompt_injection.py` is unchanged).

Same prompts as the agents package, kept local so this service can
evolve them independently of the LangGraph node body. When the two
converge in a later sprint, we'll point both at one module.

PR-D4: prompts are BUILDERS that take the live session context. The
previous constants didn't know the scenario title, the user's goal, or
which side of the conversation the user was on — the LLM drifted to
weather chitchat in the roleplay node, and the coach produced lines from
the opponent's POV instead of the user's.
"""

from __future__ import annotations

import re

import structlog

from app.llm import Message
from app.services.sessions.coach_strategy import STRATEGY_PROMPT_BLOCK
from app.services.sessions.turn_repository import CoachHintTrio, TurnRecord

logger = structlog.get_logger(__name__)


def _build_roleplay_prompt(
    *,
    scenario_title: str,
    background: str,
    persona_title: str,
    user_goal: str,
    character_descriptor: str = "",
    memory_note: str = "",
    corpus_examples: str = "",
) -> str:
    """System prompt for the AI opponent.

    Pins the LLM to the scenario, the persona, and — crucially — to
    the opposing side of the user's stated goal. Without the explicit
    "你在跟用户对立面" line, models defaulted to neutral friendly
    chitchat and forgot they were a tough negotiation counterpart.

    PR-L1.3: `character_descriptor` carries the 6-dim persona profile
    rendered as a Chinese bullet list (see
    `app.services.scenarios.character_vector.describe_for_roleplay`).
    When the vector is at the neutral baseline the descriptor is empty
    and the prompt collapses to the previous shape — so an unmigrated
    custom scenario still works end-to-end.

    PR-L6: `memory_note` carries the opponent's recall of past sessions
    in this scenario ("你之前和这个用户交手过 N 次..."). Empty on a
    first visit, so the prompt is unchanged for new (user, scenario)
    pairs.

    PR-L4: `corpus_examples` carries a few-shot block of real Chinese
    lines whose persona profile is nearest the opponent's live mood, so
    it talks in a real register instead of translation-ese. Placed last,
    just before the response rules, so it's the freshest context the
    model reads before generating. Empty when the corpus returns nothing."""
    descriptor_block = f"\n{character_descriptor}\n\n" if character_descriptor else ""
    memory_block = f"\n{memory_note}\n" if memory_note else ""
    corpus_block = f"\n{corpus_examples}\n" if corpus_examples else ""
    return (
        f"你扮演用户练习对话中的对手。场景：「{scenario_title}」。\n"
        f"场景背景：{background}\n"
        f"你的角色身份：{persona_title}。\n"
        f"用户的目标是：{user_goal}\n"
        f"{descriptor_block}"
        f"{memory_block}"
        f"{corpus_block}"
        "你站在与用户对立的一方，要让用户感受到压力，但不能爆粗、不能人身攻击。\n"
        "回应要自然、像真人说话，不超过 80 字。不要给用户建议，不要破坏角色，不要替用户说话。"
    )


def _build_coach_prompt(
    *,
    scenario_title: str,
    user_goal: str,
    opponent_profile: str = "",
) -> str:
    """System prompt for教练 K's three-tone hint.

    PR-D4: K used to drift into the opponent's voice ("我打游戏关你什么事"
    in the 室友打游戏 scenario, said as if the user *was* the gamer
    instead of the one losing sleep). Pinning the user's side via
    user_goal in the system prompt fixes the perspective.

    PR-L1.3: `opponent_profile` (3-dim subset rendered by
    `describe_for_coach`) lets K tune the hint to who the user is up
    against —硬顶 a high-power_gap boss vs the same line to a peer
    are very different advice. Empty when no dim is in the outer band
    (e.g. fallback record); prompt then reads as before."""
    opponent_block = f"\n{opponent_profile}\n\n" if opponent_profile else ""
    return (
        f"你是教练 K，正在指导【用户】练习对话。\n"
        f"场景：「{scenario_title}」\n"
        f"用户的目标：{user_goal}\n"
        f"{opponent_block}"
        "你站在【用户这一边】，给出的提示是【用户接下来要说的话】，不是对手的话，"
        "也不是替用户分析对方。每行直接是用户可以照说的一句话。\n"
        "看完对手的回应，给用户三档下一句提示，每行 ≤ 30 字，按以下格式严格输出，"
        "不要解释、不要任何额外文字：\n"
        "SAFE: <稳如老狗版>\n"
        "AGGRESSIVE: <正面刚版>\n"
        "HUMOR: <整活版>\n"
        # PR-L8: strategy read appended after the three tones.
        f"{STRATEGY_PROMPT_BLOCK}"
    )


_JUDGE_PROMPT = (
    "你是评委。看完用户与对手的对话，对【用户的话】给一个评分。\n"
    "只输出两行：\n"
    "VERDICT: shenfeng | guolu | fanche\n"
    "RATING: 0-100 的整数\n"
    "不要解释，不要任何额外文字。"
)

_COACH_SAFE_RE = re.compile(r"SAFE\s*:\s*(.+)", re.IGNORECASE)
_COACH_AGGRO_RE = re.compile(r"AGGRESSIVE\s*:\s*(.+)", re.IGNORECASE)
_COACH_HUMOR_RE = re.compile(r"HUMOR\s*:\s*(.+)", re.IGNORECASE)

_COACH_FALLBACK = CoachHintTrio(
    safe="先稳住，反问对方真正的诉求",
    aggressive="直接指出底线，不退让",
    humor="用一句玩笑把球踢回去",
)

# Used when post-stream output moderation gates the roleplay LLM's
# reply. The user already saw the deltas (they were streamed live);
# this replaces only the authoritative `opponent.done.full_text` +
# the persisted turn record, so chat history doesn't carry the bad
# text into future turns. Frontend can render it as "[opponent fell
# silent]" or similar based on the verdict_output trace tag.
_ROLEPLAY_REDACTED_PLACEHOLDER = "……"

# Canned opponent line emitted when the roleplay LLM produces nothing or
# times out (both providers missed the first-byte budget). It's our own
# safe copy, so output moderation skips it — same treatment as the
# redacted placeholder above.
_ROLEPLAY_FALLBACK_LINE = "（对面顿了顿，没接话）"


def _build_history(
    *,
    seed_opening: str,
    prior_turns: list[TurnRecord],
) -> list[Message]:
    """Reconstruct the chat history seen by the roleplay LLM.

    The scenario's opening line counts as the opponent's first turn so
    the LLM has a stance to react against. Each subsequent turn adds a
    user message and the opponent reply that came back.
    """
    history: list[Message] = [Message.assistant(seed_opening)]
    for turn in prior_turns:
        history.append(Message.user(turn.user_content))
        history.append(Message.assistant(turn.opponent_reply))
    return history


def _parse_three_tones(raw: str) -> CoachHintTrio:
    """Best-effort parse; missing fields fall back to canned safe copy."""
    safe = _COACH_SAFE_RE.search(raw)
    aggro = _COACH_AGGRO_RE.search(raw)
    humor = _COACH_HUMOR_RE.search(raw)
    if not (safe and aggro and humor):
        logger.warning("coach_unparseable", raw=raw[:200])
        return _COACH_FALLBACK
    return CoachHintTrio(
        safe=safe.group(1).strip(),
        aggressive=aggro.group(1).strip(),
        humor=humor.group(1).strip(),
    )
