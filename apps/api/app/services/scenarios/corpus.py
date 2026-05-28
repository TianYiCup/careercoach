"""Chinese conversational corpus + trait retrieval — Character Engine L4.

The data moat. A curated set of real-sounding Chinese lines, each tagged
with the 6-dim `CharacterVector` profile it fits. At roleplay time the
opponent's *live mood* (L3) retrieves the nearest few snippets, which go
into the prompt as few-shot style examples — so the opponent talks like
a real Chinese person (小红书 / 知乎 / 脱口秀 register), not translation-ese,
and the register shifts as the mood moves.

This is tag/trait retrieval, not vector-semantic RAG — distance is taken
directly over the 6 trait dimensions, no embedding model. A pgvector
layer over a 50k-line corpus is the L4.2 data-moat build; this seeds the
structure + retrieval seam + a starter set content-ops grows.

Red-line note (§3.0.5): every snippet is adversarial-but-safe — pressure,
not abuse. None touch the six red lines. New snippets pass the same
review as scenario seed copy.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.services.scenarios.character_vector import VECTOR_DIMENSIONS, CharacterVector

# How many snippets to surface as few-shot examples. Few enough to keep
# the prompt lean and not drown the scenario context; the model learns
# the register from 3 examples without parroting them.
DEFAULT_TOP_K = 3


@dataclass(frozen=True)
class CorpusSnippet:
    """One real-sounding Chinese line + the persona profile it fits."""

    text: str
    vector: CharacterVector


def _v(
    aggression: int,
    empathy: int,
    control: int,
    honesty: int,
    stability: int,
    power_gap: int,
) -> CharacterVector:
    return CharacterVector(
        aggression=aggression,
        empathy=empathy,
        control=control,
        honesty=honesty,
        stability=stability,
        power_gap=power_gap,
    )


# Starter corpus spanning the trait space. Clustered loosely by archetype
# so retrieval has a near match for any mood. Content-ops grows this to
# the 50k-line moat; the grep-friendly `# --- cluster ---` headers make
# the backfill auditable (mirrors the scenario seed convention).
CORPUS: tuple[CorpusSnippet, ...] = (
    # --- 强硬上位者 / authority pressure (high aggression+control+power_gap) ---
    CorpusSnippet("这点苦都吃不了，以后还怎么扛事？", _v(75, 25, 80, 60, 75, 80)),
    CorpusSnippet("我把话撂这儿：这事没得商量。", _v(80, 20, 85, 70, 80, 80)),
    CorpusSnippet("别跟我谈条件，先把活干漂亮了再说。", _v(78, 25, 82, 65, 78, 80)),
    CorpusSnippet("年轻人，吃亏是福，这道理还要我教你？", _v(60, 35, 78, 55, 80, 82)),
    # --- 政治化/打太极 / evasive-political (high control, low honesty) ---
    CorpusSnippet("公司有公司的考量，这个我没法给你承诺。", _v(40, 35, 72, 22, 82, 70)),
    CorpusSnippet("这事吧，得走流程，你先把材料交上来再说。", _v(35, 30, 70, 20, 80, 65)),
    CorpusSnippet("我也是替你着想，有些话点到为止啊。", _v(38, 45, 68, 25, 78, 65)),
    CorpusSnippet("领导那边我尽量帮你提，但你也知道，不好说。", _v(30, 40, 60, 25, 75, 62)),
    # --- 平辈/室友 撒娇推脱 / peer-deflection (low power_gap, low aggression) ---
    CorpusSnippet("哎呀就这一把嘛，打完我就睡，真的。", _v(25, 30, 25, 60, 45, 15)),
    CorpusSnippet("至于吗，多大点事，明天再说行不行。", _v(30, 25, 30, 55, 50, 18)),
    CorpusSnippet("你别这么严肃啊，搞得我好像欺负你了。", _v(35, 35, 35, 50, 45, 20)),
    CorpusSnippet("行行行我错了还不行吗，你别念了。", _v(28, 30, 25, 55, 40, 15)),
    # --- 焦虑长辈 / anxious-elder (high empathy+control, low stability) ---
    CorpusSnippet("妈也是为你好，你怎么就不明白呢。", _v(35, 55, 75, 70, 30, 65)),
    CorpusSnippet("你看人家小明都成家立业了，你急不急？", _v(40, 45, 72, 68, 28, 62)),
    CorpusSnippet("我跟你爸操了一辈子心，就盼你稳定。", _v(30, 60, 70, 72, 32, 65)),
    # --- 情绪不稳/一点就炸 / volatile (low stability, high aggression) ---
    CorpusSnippet("你再说一遍试试？我今天就把话说清楚。", _v(80, 25, 60, 65, 20, 40)),
    CorpusSnippet("行，你翅膀硬了是吧，那你自己看着办。", _v(75, 30, 55, 60, 22, 45)),
    CorpusSnippet("我忍你很久了，今天必须给个说法。", _v(78, 28, 58, 68, 25, 42)),
    # --- 直球/不装 / blunt-honest (high honesty+aggression) ---
    CorpusSnippet("我直说了，你这方案不行，重做。", _v(70, 30, 65, 88, 70, 60)),
    CorpusSnippet("别绕了，你到底想要什么，一句话。", _v(65, 30, 70, 85, 72, 55)),
    CorpusSnippet("丑话说前头，做不到就别接。", _v(68, 25, 68, 85, 72, 58)),
    # --- 油滑商人/客服 / slick-transactional (low honesty, stable) ---
    CorpusSnippet("这个价格已经是给您最大优惠了，真的。", _v(30, 35, 60, 25, 80, 45)),
    CorpusSnippet("您的心情我特别理解，但规定就是这样。", _v(25, 40, 58, 28, 82, 40)),
    CorpusSnippet("这样吧，我个人帮您想想办法，但不保证啊。", _v(28, 42, 55, 30, 78, 42)),
    # --- 高冷/有距离 / aloof (low empathy, stable, mid power_gap) ---
    CorpusSnippet("（看了眼手机）嗯，你说。", _v(35, 18, 45, 55, 78, 50)),
    CorpusSnippet("我没那么多时间寒暄，有事说事。", _v(45, 20, 55, 65, 80, 52)),
    # --- 摆烂/敷衍 / disengaged (low everything) ---
    CorpusSnippet("哦，知道了，回头弄。", _v(18, 25, 20, 40, 50, 20)),
    CorpusSnippet("你们先弄着，到时候我看一眼就行。", _v(20, 28, 22, 38, 52, 22)),
    # --- 共情但坚定 / warm-firm (high empathy + mid control) ---
    CorpusSnippet("我懂你不容易，但这次真的得按规矩来。", _v(35, 70, 60, 65, 75, 55)),
    CorpusSnippet("你的难处我记下了，咱们一起想个办法。", _v(25, 75, 55, 70, 72, 48)),
)


def _distance(a: CharacterVector, b: CharacterVector) -> int:
    """Manhattan distance over the 6 trait dims — cheap, no embeddings,
    and monotonic in 'how differently this snippet behaves from the
    current mood', which is all retrieval needs."""
    return sum(abs(getattr(a, name) - getattr(b, name)) for name in VECTOR_DIMENSIONS)


def retrieve(mood: CharacterVector, *, k: int = DEFAULT_TOP_K) -> list[CorpusSnippet]:
    """The `k` corpus snippets whose persona profile is nearest the
    opponent's current mood, closest first. `k <= 0` returns []."""
    if k <= 0:
        return []
    ranked = sorted(CORPUS, key=lambda s: _distance(s.vector, mood))
    return ranked[:k]


def build_corpus_examples(snippets: list[CorpusSnippet]) -> str:
    """Render retrieved snippets into the few-shot block injected into
    the roleplay prompt. Empty string for no snippets so the prompt
    collapses to its pre-L4 shape."""
    if not snippets:
        return ""
    lines = "\n".join(f"- {s.text}" for s in snippets)
    return (
        "参考下面这些真实中文语气（学语气和说话方式，不要照抄原句，"
        "也不要硬套到不相关的内容上）：\n" + lines
    )


__all__ = [
    "CORPUS",
    "DEFAULT_TOP_K",
    "CorpusSnippet",
    "build_corpus_examples",
    "retrieve",
]
