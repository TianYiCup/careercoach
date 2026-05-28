# CareerCoach AI · Character Engine — Pitch Deck 章节

> 这一章是 pitch deck 的"技术差异化 + 产品愿景"章节。
> 适合放在 vision / market sizing 之后，roadmap 之前。
> 每个 `##` 是一页 slide 的大纲；`> [visual]` 是给设计师的图示提示。

---

## Slide · 这一节回答的问题

**为什么我们不是又一个 ChatGPT 套壳？**

> [visual] 一个简洁的 H1 + 副标题，黑底荧光色

---

## Slide · 同类产品的天花板

现在市面上的 AI 对话教练 / 角色扮演工具（Character.AI、Wysa、Replika、Coze 上的 RP bot）卡在四个软肋：

| 软肋 | 表现 |
|---|---|
| **金鱼记忆** | 上一轮被骂哭，下一轮对手又笑脸相迎 |
| **平铺无张力** | 30 轮对话没有"开场 → 冲突 → 转折 → 收尾"，越聊越无聊 |
| **AI 翻译腔** | "请问您方便讨论一下此事吗" — 不是中国人说话 |
| **教练只是复读机** | 给你三个备选答案，从不识别你**正在用什么策略** |

→ 这四件事一旦让用户察觉，他下次就不打开了。**留存死在这里**。

> [visual] 4 张对比卡片：左边别家产品的真实截图，右边我们的目标体验，用红/绿对照

---

## Slide · 我们的解法 — Character Engine

不做 persona 标签库，做 **会呼吸的对手**。

> 一句话定义：**一个有性格、有情绪曲线、被你影响、记得你上次表现的人。**

不是"暴躁型 / 温和型"四个标签，而是**多维角色向量** × **戏剧节奏导演** × **真实语料 RAG** × **长期记忆**。

> [visual] 一张架构 hero 图：用户头像 ↔ 一个"会变脸"的对手 mascot，中间是 9 层电路板

---

## Slide · 三组、九层

把这 9 层拆成用户**感觉得到**的三组价值：

### 组 1 — 对手像真人

| 层 | 解法 | 用户感觉到的 |
|---|---|---|
| L1 Character Vector | persona 升级成 6-8 维数值（攻击/共情/控制/诚实/稳定/权力差）任意组合 | 不再是"四选一"，能体验"温和外表 + 控制欲拉满"这种复合角色 |
| L4 Authenticity RAG | 每个 character 配 200-500 条**真实中文语料**（小红书、知乎、脱口秀），生成时 few-shot | 对手说**中国人话**，不是翻译腔 |

### 组 2 — 对话像戏剧

| 层 | 解法 | 用户感觉到的 |
|---|---|---|
| L2 Arc Director | 一个 agent 监控"现在是开场 / 冲突 / 转折 / 收尾"，告诉 roleplay 该升还是该降 | 30 轮永远不会平铺 |
| L3 Mood Arbiter | 双 agent — 一个解析"用户这句做了什么"，一个根据 character × 用户行为 × arc 阶段计算 next mood | 对手**真的有情绪起伏**，被你激怒 / 被你说服都有反应 |
| L9 Mood UI | 对手头像下方实时**情绪气压计** + 6 维**雷达图** | 用户**看见**自己说话的效果，不是猜 |

### 组 3 — 教练真懂你

| 层 | 解法 | 用户感觉到的 |
|---|---|---|
| L5 User Profile | 记弱点 / 应答倾向 / 各 character 胜率 / 进步速度 | 新手不上满血，老手 character 针对弱点出招 |
| L6 Long-Term Memory | pgvector 存 session embedding，下次同场景对手"记得你" | "上次你也这么说，结果还是没做到" — 对手不再 7 秒记忆 |
| L8 Coach K Reimagined | 教练识别**你正在用的策略**（讨好 / 转移 / 反问 / 直球），评估有效性，推荐**升级方向** | 教练不是给答案，是真教练 |
| L7 Emotion Safety | 双层 — 浅层内容审核 + 深层"情绪伤害"评估，超阈值强制让 character 软化 | 18-25 岁产品的红线 — 练习不能变霸凌模拟 |

> [visual] 三栏并列卡片，每栏用一个 hero 字：「真人」「戏剧」「懂你」

---

## Slide · 跟竞品的差异化

|  | Character.AI | Coze RP bot | Wysa | **CareerCoach** |
|---|---|---|---|---|
| 多维 character | 角色卡描述 | 角色卡描述 | 固定语料 | **6-8 维数值，可组合** |
| 情绪状态机 | 无 | 无 | 浅 | **Mood Arbiter + Arc Director** |
| 长期记忆 | 部分 | 无 | 浅 | **pgvector + episode log** |
| 中文真实语料 | 弱 | 中 | 弱 | **RAG over 中文素材库（壁垒）** |
| 用户画像驱动难度 | 无 | 无 | 部分 | **User Profile × Character Intensity** |
| 教练反馈 | 无 | 无 | CBT 提示 | **策略识别 + 升级建议** |
| 情绪安全 | 弱 | 弱 | 强（心理学背景） | **双层 + 红线** |

→ 真正的差异化在 **L4 RAG 中文素材库** + **L3 Mood Arbiter** + **L8 Coach 重写**。其他家做不到不是技术问题，是**数据问题**和**产品想象力问题**。

> [visual] 横向对比表，重点列高亮我们的勾，竞品打灰

---

## Slide · 数据飞轮（壁垒）

```
用户练对话
   ↓
session episode + 情绪轨迹 入库
   ↓
LLM-as-judge 标注用户应答策略 + character 反应自然度
   ↓
高质量片段 → RAG 语料库
   ↓
character 表现更自然 → 留存更高
   ↓
更多用户练 ↻
```

→ **L4 RAG 语料**用越久越值钱。竞品想抄要重新冷启动 6-12 个月。

> [visual] 循环箭头图，中心 logo

---

## Slide · 工程量盘点

| 阶段 | 内容 | 工程 | 团队配置 |
|---|---|---|---|
| **Now（已完成）** | scenario × persona 笛卡尔积、prompt 注入场景 + 用户目标、Coach K 三档话术、Langfuse trace、内容审核 | 6 周 | 1 后端 + 1 前端 |
| **3 个月** | L1 多维 character、L3 Mood Arbiter（轻）、L9 Mood UI、L5 user profile 接入 character intensity | +3 个月 | +1 prompt 工程师 |
| **6 个月** | L2 Arc Director、L8 Coach 重写、L7 双层情绪安全、初版 RAG 语料库（5k 条） | +3 个月 | +1 内容运营 |
| **12 个月** | L4 RAG 全量（50k+ 条中文真实语料）、L6 长期记忆 + pgvector、跨 session character 连贯性 | +6 个月 | +1 内容 + 数据飞轮搭建 |

每轮 LLM 调用从 3-4 次 → 6-8 次，单局成本 ×2.5。我们用 **DeepSeek 主链路** + **Qwen 备份**，单局成本仍可控在 ¥0.3 以内（PRD §3.4）。

> [visual] 一个 4 段时间轴，颜色由浅到深；下方一行成本数字

---

## Slide · 三段式产品演进

```
[Demo · Now]        [v1.0 · 3 月]         [v3.0 · 12 月]

scenario × persona  Character Engine 雏形  Character Engine 完整版

— LLM 跑通          — Mood Arbiter         — RAG 中文语料库（壁垒）
— scenario 注入     — 6 维 character       — 跨 session 长期记忆
— 教练三档          — Mood UI 可视化       — User Profile 驱动难度
— 情绪标签          — Coach 识别用户策略   — 数据飞轮自循环

  够拿下评委         能让人付费             壁垒成立、可融下一轮
```

> [visual] 三个里程碑柱，从矮到高；下方各放 1 张代表性 UI mockup

---

## Slide · Now（demo 能展示的）

评委今天看到的：

1. ✅ **场景沉浸** — 对手开场就贴合 scenario（"小林啊，加个班..."），不再寒暄
2. ✅ **教练视角** — K 的三档话术都从**用户视角**给（不是替对手说话）
3. ✅ **真 LLM 链路** — DeepSeek 主线、Qwen 备份、Langfuse 全程 trace
4. ✅ **内容审核** — 红线 200 样本对抗集 nightly 跑
5. ✅ **沙盘 + 复盘 + 副驾** 三大模式
6. ✅ **教练 K 人格** — 嘴硬心软不爹味（PRD §3.0.5）

→ "暴躁老板 + 室友打游戏"这种**多维 character**是 v1.0 才有。今天演示的版本回答"我们能做出来"，pitch deck 的 character engine 章节回答"我们能做多远"。

> [visual] 1 张今日 demo 的实拍 + 1 张 v1.0 mockup 并列

---

## Slide · Asks

| 资源 | 用途 |
|---|---|
| **3 工程师 12 个月** | 完成 L1-L9 完整版（后端 + 前端 + prompt） |
| **1 内容运营 12 个月** | RAG 中文语料库采集 + 标注 + 清洗（数据壁垒） |
| **LLM 调用预算 ¥30万/年** | 50k DAU × 平均 1 局/天 × ¥0.3/局 × 50% LLM-as-judge 标注成本 |
| **¥80万/年法务 + 内容审核** | 备案、§3.0.5 红线人工复审、未成年保护 |

→ **三百万人民币**做出 Character Engine 完整版，留存指标到行业 top tier，下一轮估值可证。

> [visual] 4 张数字卡片 + 一个总金额

---

## Slide · 一句话 closing

> 同行做 **chatbot**，我们做 **会呼吸的对手**。
>
> 同行做 **persona 标签**，我们做 **角色引擎**。
>
> 同行 **6 个月被抄**，我们 **6 个月还在采语料**。

> [visual] 黑底荧光紫，3 行错落对仗

---

## 附录 · Technical Architecture Diagram

完整 LangGraph 流转（v1.0 之后）：

```
user_turn
  │
  ▼
mood_perception      ← 解析"用户这句做了什么"（讨好/反击/理性/装傻/转移）
  │
  ▼
mood_arbiter         ← 应用 character × user_behavior × arc 阶段 → next_mood
  │
  ▼
arc_director         ← 这轮该升、该降、还是收尾？
  │
  ▼
roleplay (RAG)       ← 用 next_mood + RAG 几条真实语料 → 生成回复 + 情绪标签
  │
  ▼
emotion_safety       ← 当前累积心理压力 > 阈值？强制软化
  │
  ▼
coach (strategy)     ← 识别用户当前策略 → 评估有效性 → 推荐升级
  │
  ▼
judge                ← 打分 + 更新 user profile
  │
  ▼
memory_writer        ← 写 session episode + 标注高质量片段进 RAG 候选
```

7 个 agent，每轮可并行的 3-4 个走 batch；端到端首字节延迟控制在 1.2s 内（PRD §3.4.1 NFR）。

> [visual] LangGraph 流程图，节点用我们的 cyber 色板上色
