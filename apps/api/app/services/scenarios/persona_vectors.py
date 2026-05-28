"""Per-scenario 6-dim persona profiles — Character Engine L1.2.

Maps every catalog ``scenario_id`` (sc_001 → sc_040) to a curated
``CharacterVector``. ``seed_data`` merges these into ``SCENARIO_CATALOG``
at module load via ``dataclasses.replace``, so each row still presents
as a single ``ScenarioRecord`` everywhere downstream.

Splitting the vector table out of ``seed_data`` keeps both files
focused:
  * ``seed_data.py`` carries the picker copy — title, opening line,
    background, tags. This is what content-ops edits.
  * ``persona_vectors.py`` carries the 6-dim profiles. This is what
    prompt-engineering tunes when an opener feels too flat or too
    aggressive across scenarios.

Dimension meaning (mirrors ``character_vector.CharacterVector``):

* ``aggression`` 攻击性 — confrontational vs measured
* ``empathy``    共情   — perceives / responds to user emotion
* ``control``    控制欲 — steers the conversation vs goes with the flow
* ``honesty``    诚实   — straight talk vs evasive / political speech
* ``stability``  稳定   — composed under push-back vs reactive
* ``power_gap``  权力差 — hierarchical advantage over the user

Values cluster as 0-20 dormant / 21-40 low / 41-60 baseline /
61-80 strong / 81-100 dominant. The curated values below stay inside
the 15-85 range — saving 0 and 100 for personas in later epics that
need a pure-archetype profile (a complete pacifist, a tyrant) without
overlap with the day-one catalog.

When you add a new ``ScenarioRecord`` to ``seed_data.SCENARIO_CATALOG``,
add its entry here too. ``tests/test_character_vector.py`` guards
coverage — every catalog id must have a curated (non-neutral) vector
once L1.2 ships.
"""

from __future__ import annotations

from typing import Final

from app.services.scenarios.character_vector import CharacterVector

# Keep the dict literal sorted by scenario_id so a future diff stays
# review-friendly — alphabetical on the id is the same order as the
# catalog itself, so reviewers can scroll the two files in parallel.
PERSONA_VECTORS: Final[dict[str, CharacterVector]] = {
    # --- PRD §1.3 demo trio (originally inline in seed_data; moved here
    # for symmetry with the L1.2 backfill so all 40 vectors live in one
    # place) ---
    "sc_001": CharacterVector(
        aggression=60, empathy=30, control=75, honesty=50, stability=80, power_gap=70
    ),
    "sc_002": CharacterVector(
        aggression=40, empathy=35, control=70, honesty=30, stability=85, power_gap=65
    ),
    "sc_003": CharacterVector(
        aggression=25, empathy=20, control=20, honesty=70, stability=45, power_gap=15
    ),
    # --- 校园 campus ---
    "sc_004": CharacterVector(  # 强势导师 — 画饼让你干私活
        aggression=50, empathy=35, control=80, honesty=25, stability=75, power_gap=80
    ),
    "sc_008": CharacterVector(  # 忙起来不回消息的导师 — 拖延 / 推托
        aggression=25, empathy=30, control=55, honesty=45, stability=70, power_gap=75
    ),
    "sc_009": CharacterVector(  # 摆烂的组员 — 敷衍承诺
        aggression=15, empathy=25, control=20, honesty=40, stability=50, power_gap=15
    ),
    "sc_010": CharacterVector(  # 不拘小节的室友 — 钝感
        aggression=20, empathy=15, control=25, honesty=65, stability=55, power_gap=15
    ),
    "sc_011": CharacterVector(  # 强势的竞争对手 — 当众挑战
        aggression=70, empathy=25, control=60, honesty=70, stability=70, power_gap=30
    ),
    "sc_012": CharacterVector(  # 油滑的兼职老板 — 推脱话术
        aggression=30, empathy=20, control=60, honesty=20, stability=65, power_gap=60
    ),
    "sc_013": CharacterVector(  # 挑剔的评审老师 — 质疑含金量
        aggression=55, empathy=30, control=65, honesty=65, stability=75, power_gap=75
    ),
    "sc_014": CharacterVector(  # 按规矩办事的辅导员 — 不通融
        aggression=30, empathy=35, control=70, honesty=75, stability=80, power_gap=60
    ),
    "sc_015": CharacterVector(  # 不好意思直说的室友 — 旁敲侧击
        aggression=20, empathy=50, control=35, honesty=35, stability=55, power_gap=15
    ),
    "sc_016": CharacterVector(  # 不想加人的教授 — 推脱
        aggression=30, empathy=25, control=65, honesty=60, stability=70, power_gap=75
    ),
    "sc_017": CharacterVector(  # 爱使唤人的师兄 — 摆架子
        aggression=35, empathy=25, control=65, honesty=60, stability=60, power_gap=45
    ),
    # --- 求职 jobhunt ---
    "sc_005": CharacterVector(  # 资深面试官 — 中性、考察型
        aggression=30, empathy=50, control=60, honesty=60, stability=80, power_gap=60
    ),
    "sc_018": CharacterVector(  # 强势抢话的候选人 — 主导讨论
        aggression=60, empathy=20, control=75, honesty=55, stability=60, power_gap=20
    ),
    "sc_019": CharacterVector(  # 压价的 HR — 强硬还价
        aggression=35, empathy=30, control=75, honesty=35, stability=85, power_gap=65
    ),
    "sc_020": CharacterVector(  # 面带微笑的面试官 — 礼貌探询
        aggression=20, empathy=55, control=55, honesty=65, stability=80, power_gap=60
    ),
    "sc_021": CharacterVector(  # 不想被拿捏的 HR — 还价 / 试探
        aggression=45, empathy=30, control=70, honesty=50, stability=80, power_gap=55
    ),
    "sc_022": CharacterVector(  # 一脸为难的 HR — 推脱、画饼
        aggression=20, empathy=45, control=55, honesty=35, stability=60, power_gap=55
    ),
    "sc_023": CharacterVector(  # 盯着简历的面试官 — 追问
        aggression=45, empathy=30, control=70, honesty=65, stability=80, power_gap=65
    ),
    "sc_024": CharacterVector(  # 把活推出去的组员 — 甩锅
        aggression=30, empathy=30, control=50, honesty=30, stability=55, power_gap=20
    ),
    "sc_025": CharacterVector(  # 赶时间的 HR — 不耐烦
        aggression=35, empathy=20, control=50, honesty=55, stability=50, power_gap=45
    ),
    # --- 实习 intern ---
    "sc_026": CharacterVector(  # 没空带你的实习导师 — 敷衍
        aggression=20, empathy=25, control=50, honesty=50, stability=60, power_gap=65
    ),
    "sc_027": CharacterVector(  # 不想沾事的隔壁组同事 — 推开
        aggression=25, empathy=25, control=35, honesty=45, stability=60, power_gap=35
    ),
    "sc_028": CharacterVector(  # 直言不讳的正职同事 — 直球批评
        aggression=50, empathy=35, control=50, honesty=85, stability=70, power_gap=35
    ),
    "sc_029": CharacterVector(  # 评价谨慎的实习导师 — 保守
        aggression=20, empathy=40, control=55, honesty=55, stability=75, power_gap=60
    ),
    "sc_030": CharacterVector(  # 爱画饼的导师 — 画饼 / 变相白嫖
        aggression=25, empathy=40, control=65, honesty=20, stability=70, power_gap=65
    ),
    "sc_031": CharacterVector(  # 有点高冷的正职同事 — 距离感
        aggression=20, empathy=25, control=35, honesty=55, stability=75, power_gap=30
    ),
    "sc_032": CharacterVector(  # 不愿表态的导师 — 政治化、绕话题
        aggression=20, empathy=35, control=55, honesty=25, stability=75, power_gap=60
    ),
    "sc_033": CharacterVector(  # 急着甩锅的同事 — 自保
        aggression=55, empathy=20, control=55, honesty=25, stability=50, power_gap=40
    ),
    "sc_034": CharacterVector(  # 习惯加班的组长 — 阴阳怪气
        aggression=45, empathy=25, control=55, honesty=50, stability=65, power_gap=60
    ),
    # --- 生活 life ---
    "sc_006": CharacterVector(  # 操心的母亲 — 焦虑型推力
        aggression=35, empathy=55, control=75, honesty=75, stability=30, power_gap=65
    ),
    "sc_007": CharacterVector(  # 精打细算的房东 — 冷漠商人
        aggression=45, empathy=15, control=65, honesty=40, stability=75, power_gap=55
    ),
    "sc_035": CharacterVector(  # 滴水不漏的客服 — 流程话术
        aggression=25, empathy=35, control=60, honesty=30, stability=85, power_gap=35
    ),
    "sc_036": CharacterVector(  # 锱铢必较的房东 — 抠押金
        aggression=50, empathy=15, control=65, honesty=35, stability=70, power_gap=55
    ),
    "sc_037": CharacterVector(  # 得寸进尺的买家 — 二手压价
        aggression=40, empathy=15, control=55, honesty=25, stability=55, power_gap=20
    ),
    "sc_038": CharacterVector(  # 装糊涂的朋友 — 借钱不还
        aggression=15, empathy=35, control=30, honesty=15, stability=55, power_gap=15
    ),
    "sc_039": CharacterVector(  # 还在赌气的对象 — 阴阳怪气
        aggression=50, empathy=60, control=40, honesty=30, stability=35, power_gap=15
    ),
    "sc_040": CharacterVector(  # 不好拒绝的长辈亲戚 — 道德绑架
        aggression=35, empathy=40, control=55, honesty=50, stability=60, power_gap=50
    ),
}


__all__ = ["PERSONA_VECTORS"]
