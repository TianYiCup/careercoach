"""Coach strategy taxonomy + parsing — Character Engine L8.

Coach K stops being a three-option vending machine. On top of the
SAFE / AGGRESSIVE / HUMOR next-line hints it now reads *what tactic the
user just played*, judges whether it landed, and points at an upgrade —
the difference between "here are 3 things to say" and a coach who can
see your game.

The taxonomy is a **closed set** (the L8 design choice): a fixed list
of strategy + effectiveness keys. Closed because:
  * the labels need to stay consistent across turns so L5 (user
    profile) can aggregate "this user leans on 讨好 and it keeps
    backfiring" — free-text labels drift and can't be counted;
  * the frontend renders a fixed Chinese gloss per key, so the wire
    stays English/stable and i18n-safe (same pattern as ScoreResult's
    pinyin).

The LLM is told to pick from the keys (with the Chinese gloss inline in
the prompt). The parser matches the English keys; anything off-list →
None, and the caller drops the strategy card rather than guessing.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# key -> Chinese display label. Order is the rough "soft → hard" spectrum
# so a future UI could lay them on an axis. Keys are the wire contract;
# the frontend owns the gloss too (kept in sync via the contract test).
STRATEGY_LABELS: dict[str, str] = {
    "placate": "讨好",
    "concede": "示弱让步",
    "avoid": "回避",
    "deflect": "转移话题",
    "counter": "反问",
    "reason": "讲道理",
    "direct": "直球",
}

# Did the strategy work against the opponent's last move?
EFFECT_LABELS: dict[str, str] = {
    "good": "奏效",
    "mixed": "部分奏效",
    "poor": "没奏效",
}


@dataclass(frozen=True)
class CoachStrategyRead:
    """K's read of the user's just-played turn.

    `strategy` is what they did, `effect` is whether it landed, `upgrade`
    is the recommended next tactic. `upgrade == strategy` means "keep
    doing this" — the UI renders that as 继续 rather than 试试."""

    strategy: str  # a STRATEGY_LABELS key
    effect: str  # an EFFECT_LABELS key
    upgrade: str  # a STRATEGY_LABELS key

    def to_dict(self) -> dict[str, str]:
        return {"strategy": self.strategy, "effect": self.effect, "upgrade": self.upgrade}


# Prompt fragment appended to the coach system prompt. Lists the closed
# keys with their Chinese gloss so the model picks on-vocabulary.
STRATEGY_PROMPT_BLOCK = (
    "另外，识别【用户刚说的那句话】用的是哪种沟通策略，判断它对当前这个对手有没有奏效，"
    "并推荐下一句该升级到哪种策略。策略只能从下面这组里选英文词：\n"
    "placate(讨好) / concede(示弱让步) / avoid(回避) / deflect(转移话题) / "
    "counter(反问) / reason(讲道理) / direct(直球)\n"
    "有效性只能选：good(奏效) / mixed(部分奏效) / poor(没奏效)\n"
    "在三档提示之后，再追加三行，严格用英文键：\n"
    "STRATEGY: <用户刚用的策略键>\n"
    "EFFECT: <有效性键>\n"
    "UPGRADE: <推荐下一句的策略键，可以和 STRATEGY 相同表示保持>"
)

_STRATEGY_RE = re.compile(r"STRATEGY\s*[:：]\s*([a-zA-Z]+)", re.IGNORECASE)
_EFFECT_RE = re.compile(r"EFFECT\s*[:：]\s*([a-zA-Z]+)", re.IGNORECASE)
_UPGRADE_RE = re.compile(r"UPGRADE\s*[:：]\s*([a-zA-Z]+)", re.IGNORECASE)


def parse_strategy_read(raw: str) -> CoachStrategyRead | None:
    """Pull the three strategy lines out of the coach output. Returns
    None if any line is missing or off-vocabulary — the caller then
    omits the strategy card rather than render a guess."""
    strategy_m = _STRATEGY_RE.search(raw)
    effect_m = _EFFECT_RE.search(raw)
    upgrade_m = _UPGRADE_RE.search(raw)
    if not (strategy_m and effect_m and upgrade_m):
        return None

    strategy = strategy_m.group(1).lower()
    effect = effect_m.group(1).lower()
    upgrade = upgrade_m.group(1).lower()

    if strategy not in STRATEGY_LABELS:
        return None
    if effect not in EFFECT_LABELS:
        return None
    if upgrade not in STRATEGY_LABELS:
        return None

    return CoachStrategyRead(strategy=strategy, effect=effect, upgrade=upgrade)


__all__ = [
    "EFFECT_LABELS",
    "STRATEGY_LABELS",
    "STRATEGY_PROMPT_BLOCK",
    "CoachStrategyRead",
    "parse_strategy_read",
]
